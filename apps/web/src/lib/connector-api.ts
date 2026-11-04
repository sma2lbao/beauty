/**
 * 连接器数据层:同源 /api/connector/*(API 网关 → connector-service /v1/*)。
 *
 * 网关把 `/api/connector/<rest>` 转发到 connector-service 的 `/v1/<rest>`,
 * 所以这里只写服务内路径(如 `/connections/{id}`)。
 *
 * 契约(docs/design/connector.md §5.3/§5.7):
 * - provider 目录来自后端 manifest,含工具清单与风险等级;目录响应可能是
 *   `{providers:[...]}` 或裸数组,provider key 可能叫 `name` 或 `provider`,
 *   这里统一归一成前端形状,页面不必感知后端包封差异;
 * - 连接的凭证字段(credentials)**永不出口**,读取接口不返回;
 * - 授权走浏览器 OAuth:POST authorize 拿 authorize_url,前端整页跳转,
 *   完成后后端 302 回 `/connectors?provider=..&status=connected|error&code=..`;
 * - check/refresh/PATCH/DELETE 统一返回连接对象(旧契约的 check 结果形状仍兼容);
 * - 只有授权/scope 类错误才引导「重新连接」,上游限流/超时/错误只提示信息。
 */
import { ApiError, createHttpClient } from '@/lib/http';

/** provider manifest 里的一个工具。 */
export type ConnectorTool = {
  name: string;
  /** 人类可读名(后端目录会带,旧契约没有) */
  title?: string | null;
  description: string;
  /** 风险等级:read | write | danger(后端也可能给大写,展示层归一)。 */
  risk?: string | null;
};

/** 目录里附带的最小连接摘要。 */
export type ConnectorConnectionBrief = {
  id: string;
  status: ConnectorStatus | string;
  account: string;
  display_name?: string | null;
  enabled: boolean;
  expires_at?: string | null;
  last_checked_at?: string | null;
  last_error_code?: string | null;
};

/** GET /connectors 的 provider 目录项(已归一的形状)。 */
export type ConnectorProvider = {
  provider: string;
  display_name: string;
  description: string;
  /** 授权方式:oauth_user | oauth_tenant | token | … */
  auth_kind: string;
  /** 期望的 OAuth scope 清单 */
  scopes: string[];
  tools: ConnectorTool[];
  /** 后端是否已配置该 provider 的应用凭证;缺省视为已配置 */
  configured?: boolean;
  /**
   * 支持直接粘贴静态令牌建立连接(如 Notion internal integration)。
   * 这类 provider 不需要应用凭证:configured=false 也能连接。
   */
  token_connectable?: boolean;
  docs_url?: string | null;
  /** 目录附带的「存在 active 且 enabled 连接」标记 */
  connected?: boolean;
  connection?: ConnectorConnectionBrief | null;
};

/** 后端目录原始项:兼容 `name`/`provider` 两种 provider key 命名。 */
type RawConnectorProvider = {
  provider?: string;
  name?: string;
  display_name?: string;
  description?: string;
  auth_kind?: string;
  scopes?: string[] | null;
  tools?: ConnectorTool[] | null;
  configured?: boolean;
  token_connectable?: boolean;
  docs_url?: string | null;
  connected?: boolean;
  connection?: ConnectorConnectionBrief | null;
};

/** 后端目录响应:当前实现是 `{providers}`,旧契约/文档是裸数组。 */
type RawProviderCatalog =
  | RawConnectorProvider[]
  | { providers?: RawConnectorProvider[] };

/** 连接状态:active 可用;expired 需重新连接;revoked 已撤销。 */
export type ConnectorStatus = 'active' | 'expired' | 'revoked';

/** 一条用户连接(凭证永不返回)。 */
export type ConnectorConnection = {
  id: string;
  provider: string;
  status: ConnectorStatus | string;
  /** 第三方账号标识(如邮箱) */
  account?: string | null;
  /** 用户自定义显示名 */
  display_name?: string | null;
  enabled?: boolean;
  auth_kind?: string | null;
  /** provider 侧稳定账号 ID(各 provider 自己的用户/机器人标识) */
  provider_account_id?: string | null;
  /** provider 侧工作区/租户 ID(各 provider 自己的组织标识) */
  external_workspace?: string | null;
  /** 已授予的 scope */
  scopes?: string[] | null;
  /** 期望的 scope(当前连接 DTO 不返回,展示时从 provider 目录补) */
  expected_scopes?: string[] | null;
  /** 连接上快照的工具(当前连接 DTO 不返回,展示时从 provider 目录补) */
  tools?: ConnectorTool[] | null;
  /** 发起授权的用户(owner);当前连接 DTO 不返回 */
  owner_subject?: string | null;
  expires_at?: string | null;
  refresh_expires_at?: string | null;
  last_refreshed_at?: string | null;
  last_checked_at?: string | null;
  /** 最近一次失败的错误码(如 connector.auth_expired) */
  last_error_code?: string | null;
  revoked_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

/** 发起浏览器授权的结果(session_id 仅旧契约返回)。 */
export type ConnectorAuthorizeRead = {
  provider?: string;
  session_id?: string | null;
  authorize_url: string;
  expires_at?: string | null;
};

/** POST refresh 的结果:新契约返回连接对象,旧契约只返回过期时间。 */
export type ConnectorRefreshRead =
  | ConnectorConnection
  | { expires_at?: string | null; last_refreshed_at?: string | null };

/** POST check 的结果:新契约返回连接对象(错误码写在 last_error_code),旧契约返回 {ok,...}。 */
export type ConnectorCheckRead =
  | ConnectorConnection
  | {
      ok: boolean;
      detail?: string | null;
      error_code?: string | null;
      checked_at?: string | null;
    };

/** 归一后的连通性检查结论。 */
export type ConnectorCheckOutcome = {
  ok: boolean;
  detail: string | null;
  reconnect: boolean;
};

export type ConnectorAuthorizeInput = { redirect_uri?: string };

/** 粘贴静态令牌建立连接:令牌只随这一次请求发送,前端不缓存。 */
export type ConnectorConnectTokenInput = {
  provider: string;
  token: string;
  display_name?: string;
};

export type ConnectorUpdateInput = {
  display_name?: string;
  enabled?: boolean;
};

/**
 * 授权/scope 类错误码(doc §5.7):只有这些引导用户「重新连接」;
 * upstream_rate_limited / upstream_timeout / upstream_error 等只显示错误信息。
 */
export const RECONNECT_ERROR_CODES = [
  'connector.auth_required',
  'connector.auth_expired',
  'connector.auth_revoked',
  'connector.scope_missing',
] as const;

export function isReconnectCode(code?: string | null): boolean {
  return (
    typeof code === 'string' &&
    (RECONNECT_ERROR_CODES as readonly string[]).includes(code)
  );
}

/** 从任意错误里取后端错误码(非 ApiError 返回 null)。 */
export function errorCodeOf(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null;
}

/** 该错误是否应该给「重新连接」入口。 */
export function isReconnectError(error: unknown): boolean {
  return isReconnectCode(errorCodeOf(error));
}

function normalizeProvider(
  raw: RawConnectorProvider,
): ConnectorProvider | null {
  const provider = raw.provider ?? raw.name ?? '';
  if (!provider) return null;
  return {
    provider,
    display_name: raw.display_name || provider,
    description: raw.description ?? '',
    auth_kind: raw.auth_kind ?? '',
    scopes: Array.isArray(raw.scopes) ? raw.scopes : [],
    tools: Array.isArray(raw.tools) ? raw.tools : [],
    configured: raw.configured,
    token_connectable: raw.token_connectable,
    docs_url: raw.docs_url ?? null,
    connected: raw.connected,
    connection: raw.connection ?? null,
  };
}

/**
 * 把 provider 目录响应归一成数组:兼容 `{providers: [...]}` 与裸数组,
 * 以及 provider 字段叫 `name` 的命名差异。
 */
export function normalizeProviderCatalog(
  catalog: RawProviderCatalog | null | undefined,
): ConnectorProvider[] {
  const raw = Array.isArray(catalog) ? catalog : (catalog?.providers ?? []);
  return raw
    .map(normalizeProvider)
    .filter((provider): provider is ConnectorProvider => provider !== null);
}

/** 把 check 的两种响应形状归一到统一结论。 */
export function checkOutcome(
  result: ConnectorCheckRead,
): ConnectorCheckOutcome {
  if ('ok' in result && typeof result.ok === 'boolean') {
    return {
      ok: result.ok,
      detail: result.detail ?? null,
      reconnect: !result.ok && isReconnectCode(result.error_code),
    };
  }
  const connection = result as ConnectorConnection;
  return {
    ok: !connection.last_error_code,
    detail: connection.last_error_code ?? null,
    reconnect: isReconnectCode(connection.last_error_code),
  };
}

/**
 * OAuth 回调地址:后端在授权完成后 302 到 `origin + /connectors`,
 * 并附加 provider/status/code/connection_id 查询参数,页面读取后 toast 并清理。
 */
export function connectorsReturnUrl(origin?: string): string {
  const base =
    origin ?? (typeof window === 'undefined' ? '' : window.location.origin);
  return `${base}/connectors`;
}

const http = createHttpClient('/api/connector');

function connectionPath(id: string): string {
  return `/connections/${encodeURIComponent(id)}`;
}

export const connectorApi = {
  /** provider 目录(manifest),含每个 provider 的工具清单。 */
  listProviders: async (): Promise<ConnectorProvider[]> =>
    normalizeProviderCatalog(
      await http.request<RawProviderCatalog>('/connectors'),
    ),
  /** 发起浏览器授权:返回 authorize_url,由调用方整页跳转。 */
  authorize: (provider: string, input: ConnectorAuthorizeInput = {}) =>
    http.request<ConnectorAuthorizeRead>(
      `/connectors/${encodeURIComponent(provider)}/authorize`,
      { method: 'POST', body: JSON.stringify(input) },
    ),
  listConnections: () => http.request<ConnectorConnection[]>('/connections'),
  /**
   * 用静态令牌建立连接(provider 需 token_connectable):
   * POST /connections → 201 ConnectionRead(auth_kind="token")。
   * 令牌只进请求体,调用方不得写入 localStorage/任何缓存。
   */
  connectWithToken: (input: ConnectorConnectTokenInput) =>
    http.request<ConnectorConnection>('/connections', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  getConnection: (id: string) =>
    http.request<ConnectorConnection>(connectionPath(id)),
  updateConnection: (id: string, patch: ConnectorUpdateInput) =>
    http.request<ConnectorConnection>(connectionPath(id), {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  removeConnection: (id: string) =>
    http.request<ConnectorConnection | undefined>(connectionPath(id), {
      method: 'DELETE',
    }),
  refreshConnection: (id: string) =>
    http.request<ConnectorRefreshRead>(`${connectionPath(id)}/refresh`, {
      method: 'POST',
    }),
  checkConnection: (id: string) =>
    http.request<ConnectorCheckRead>(`${connectionPath(id)}/check`, {
      method: 'POST',
    }),
};

export type ConnectorApi = typeof connectorApi;
