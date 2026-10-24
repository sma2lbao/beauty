/**
 * MCP Client 数据层:同源 /api/v1/mcp/servers(BFF → orchestrator)。
 *
 * 两种传输方式共用同一组接口:
 * - http:远程 Streamable HTTP server(url[/token]);
 * - stdio:本地进程(command/args/env/runtime/image),由 orchestrator 沙箱拉起。
 * 环境变量只写不读:读取接口只返回 has_env/env_keys,永远不会回显值。
 */
import { createHttpClient } from '@/lib/http';

export type MCPTool = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
};

export type MCPTransport = 'http' | 'stdio';

/** stdio server 的运行时分发:python 走 uvx/python,node 走 npx/node。 */
export type MCPRuntime = 'python' | 'node';

export type MCPServer = {
  id: string;
  name: string;
  transport: MCPTransport;
  /** http server 的地址;stdio 恒为空字符串(后端读取契约) */
  url: string;
  enabled: boolean;
  /** 免确认执行:置 true 后工具在会话里可直接执行(任务里也不再逐次确认) */
  auto_approve: boolean;
  has_token: boolean;
  token_hint: string | null;
  /** stdio 的启动命令;http 为 null */
  command: string | null;
  args: string[];
  runtime: MCPRuntime;
  /** 覆盖默认沙箱镜像;null 表示按 runtime 选默认镜像 */
  image: string | null;
  /** 是否配置了环境变量(只有值不读,读取结果只给名字) */
  has_env: boolean;
  env_keys: string[];
  tools_allowlist: string[];
  tools: MCPTool[];
  timeout_seconds: number;
  /** ok | error | auth_required(服务端回 401,需要重新授权) */
  last_status: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  /** 曾发起过浏览器授权(已动态注册 client):仅这类 http server 展示授权入口 */
  has_oauth: boolean;
  /** 当前是否持有可用的 OAuth 访问令牌 */
  oauth_authorized: boolean;
  /** 授权服务器批准的范围 */
  oauth_scopes: string[];
  /** 访问令牌过期时间;null 表示无过期时间或尚未授权 */
  oauth_expires_at: string | null;
  /** 动态注册得到的授权服务器标识(issuer) */
  oauth_authorization_server: string | null;
  /** 最近一次授权/刷新失败的说明;null 表示无异常 */
  oauth_error: string | null;
  created_at: string;
};

type MCPServerCreateBase = {
  name: string;
  timeout_seconds?: number;
  /** 免确认执行:信任该 server(默认 false,工具按危险操作仍需确认) */
  auto_approve?: boolean;
};

export type MCPServerHttpCreateInput = MCPServerCreateBase & {
  transport?: 'http';
  url: string;
  token?: string;
};

export type MCPServerStdioCreateInput = MCPServerCreateBase & {
  transport: 'stdio';
  command: string;
  args?: string[];
  env?: Record<string, string>;
  runtime?: MCPRuntime;
  image?: string;
};

/** 注册 MCP server 的入参:按 transport 判别,不会混传两套字段。 */
export type MCPServerCreateInput =
  | MCPServerHttpCreateInput
  | MCPServerStdioCreateInput;

export type MCPServerUpdateInput = {
  enabled?: boolean;
  auto_approve?: boolean;
  url?: string | null;
};

/** 浏览器授权启动结果:前端整页跳到 authorize_url,回调由 BFF 转回 /mcp?oauth=… */
export type MCPOAuthStartRead = {
  authorize_url: string;
  /** authorize_url 的有效期(秒) */
  expires_in: number;
};

export type MCPTestRead = {
  ok: boolean;
  detail: string;
  tools: MCPTool[];
  /** 服务端回 401:需要(重新)授权,页面要给重新授权指引而不是笼统失败 */
  auth_required: boolean;
};

const http = createHttpClient('/api/v1/mcp/servers');

/** HTTP 传输只接受 http(s) URL(后端同样是 ^https?:// 校验)。 */
export function isHttpUrl(value: string): boolean {
  return /^https?:\/\/\S+$/.test(value.trim());
}

/** stdio 命令只允许可执行名与相对路径(与后端 COMMAND_PATTERN 一致)。 */
export function isStdioCommand(value: string): boolean {
  return /^[A-Za-z0-9._/-]+$/.test(value.trim());
}

/** 参数输入框一行一个参数;空行忽略,保留参数自身的空格内容(仅去首尾空白)。 */
export function parseArgsText(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/**
 * 环境变量输入框按 KEY=VALUE 一行一个解析;空行与 # 注释行忽略,
 * 没有 = 或键为空的行也忽略(值可以是空字符串,等号后内容原样保留)。
 */
export function parseEnvText(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const separator = line.indexOf('=');
    if (separator <= 0) continue;
    const key = line.slice(0, separator).trim();
    if (!key) continue;
    env[key] = line.slice(separator + 1).trim();
  }
  return env;
}

export const mcpApi = {
  list: () => http.request<MCPServer[]>(''),
  create: (input: MCPServerCreateInput) =>
    http.request<MCPServer>('', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  update: (id: string, patch: MCPServerUpdateInput) =>
    http.request<MCPServer>('/' + id, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  remove: (id: string) => http.request<void>('/' + id, { method: 'DELETE' }),
  test: (id: string) =>
    http.request<MCPTestRead>('/' + id + '/test', { method: 'POST' }),
  /** 发起浏览器 OAuth 授权:拿到授权页地址,由调用方整页跳转 */
  startOAuth: (id: string) =>
    http.request<MCPOAuthStartRead>('/' + id + '/oauth/start', {
      method: 'POST',
    }),
  /** 解除浏览器授权(丢弃本地 token,server 保留) */
  disconnectOAuth: (id: string) =>
    http.request<void>('/' + id + '/oauth/disconnect', { method: 'POST' }),
};
