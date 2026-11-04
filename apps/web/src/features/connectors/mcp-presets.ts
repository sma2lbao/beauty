/**
 * MCP 预设连接器:后端内置的可一键接入清单(GET /api/v1/mcp/presets)。
 *
 * 与旧 connector-service 目录的区别:预设不是「每个 provider 配应用凭证」,
 * 而是「幂等创建一个 MCP server(POST /mcp/presets/{key})再走该 server 的
 * 浏览器 OAuth」;授权完成后浏览器回到 /connectors?oauth=success&server=<name>。
 * 这里只放数据层与纯函数,状态/动作在 use-connector-presets 里。
 */
import { createHttpClient } from '@/lib/http';
import type { MCPServer } from '@/lib/mcp-api';

/** 一个内置 MCP 预设(Notion 等):字段与后端 MCPPresetRead 对齐。 */
export type McpPreset = {
  key: string;
  name: string;
  /** 预设的远程 MCP 地址(与已建 server.url 对应,用于把卡片关联到 server) */
  url: string;
  description: string;
  /** 官方接入文档;null 表示没有 */
  docs_url: string | null;
};

/** GET /mcp/presets 的两种可能形状(裸数组 / 包体),前端都兼容。 */
type McpPresetListResponse = McpPreset[] | { presets?: McpPreset[] };

const http = createHttpClient('/api/v1/mcp/presets');

/** 授权完成后的回跳路径:后端按白名单校验,只允许站内页面。 */
export const CONNECTORS_RETURN_PATH = '/connectors';

function normalizePreset(raw: McpPreset): McpPreset {
  return {
    key: raw.key,
    name: raw.name,
    url: raw.url,
    description: raw.description ?? '',
    docs_url: raw.docs_url ?? null,
  };
}

export const mcpPresetsApi = {
  list: async (): Promise<McpPreset[]> => {
    const data = await http.request<McpPresetListResponse>('');
    const items = Array.isArray(data) ? data : (data?.presets ?? []);
    return items.map(normalizePreset);
  },
  /** 幂等创建预设对应的 MCP server;已存在时后端直接返回既有 server。 */
  ensure: (key: string) =>
    http.request<MCPServer>('/' + encodeURIComponent(key), { method: 'POST' }),
};

/** URL 归一化:去首尾空白与末尾斜杠(与后端 preset_for_url 的匹配口径一致)。 */
export function normalizeMcpUrl(value: string | null | undefined): string {
  return (value ?? '').trim().replace(/\/+$/, '');
}

/**
 * 把预设关联到已建 server:先按 URL 匹配(后端幂等创建以 URL 为准),
 * 再退回按名字忽略大小写匹配(用户手工建过同名 server 的情况)。
 */
export function matchPresetServer(
  preset: McpPreset,
  servers: MCPServer[],
): MCPServer | null {
  const url = normalizeMcpUrl(preset.url);
  const byUrl = servers.find(
    (server) =>
      server.transport === 'http' && normalizeMcpUrl(server.url) === url,
  );
  if (byUrl) return byUrl;
  const name = preset.name.trim().toLowerCase();
  return (
    servers.find(
      (server) =>
        server.transport === 'http' &&
        server.name.trim().toLowerCase() === name,
    ) ?? null
  );
}

/** 卡片四态:未创建 / 待授权(含授权失败) / 已连接 / 已停用。 */
export type McpPresetCardState =
  | 'not_linked'
  | 'needs_auth'
  | 'connected'
  | 'disabled';

/** 已建 server 但授权没走完,或最近一次授权/刷新失败。 */
export function needsReauthorize(server: MCPServer): boolean {
  return (
    !server.oauth_authorized ||
    server.last_status === 'auth_required' ||
    Boolean(server.oauth_error)
  );
}

export function presetCardState(server: MCPServer | null): McpPresetCardState {
  if (!server) return 'not_linked';
  if (!server.enabled) return 'disabled';
  if (server.oauth_authorized && !needsReauthorize(server)) return 'connected';
  return 'needs_auth';
}
