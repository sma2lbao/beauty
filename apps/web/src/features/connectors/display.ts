/**
 * 连接器的展示型派生:状态/标签/tone、scope 差集、工具风险、卡片分组与回调文案。
 * 纯函数,卡片网格与详情抽屉共用,便于单测。
 */
import type {
  ConnectorConnection,
  ConnectorProvider,
  ConnectorTool,
} from '@/lib/connector-api';
import { isReconnectCode } from '@/lib/connector-api';
import type { Tone } from '@/lib/presentation';

/** 一张 provider 卡片的状态:未连接 / 已连接 / 已停用 / 需要重新连接。 */
export type ConnectionState = 'connected' | 'disabled' | 'reconnect' | 'none';

export const CONNECTION_STATE_LABELS: Record<ConnectionState, string> = {
  connected: '已连接',
  disabled: '已停用',
  reconnect: '需要重新连接',
  none: '未连接',
};

export const CONNECTION_STATE_TONES: Record<ConnectionState, Tone> = {
  connected: 'success',
  disabled: 'neutral',
  reconnect: 'warning',
  none: 'neutral',
};

/**
 * 状态推导:授权失效优先于停用——失效要先重连,停用只是用户主动关掉了。
 * 后端也把最近一次 auth 类错误放在 last_error_code,同样判为需要重连。
 */
export function connectionState(
  connection?: ConnectorConnection | null,
): ConnectionState {
  if (!connection) return 'none';
  if (isReconnectCode(connection.last_error_code)) return 'reconnect';
  if (connection.status === 'expired' || connection.status === 'revoked') {
    return 'reconnect';
  }
  if (connection.enabled === false) return 'disabled';
  return connection.status === 'active' ? 'connected' : 'reconnect';
}

/** 授权方式文案;未知取值原样展示,便于排查新 provider。 */
export function authKindLabel(authKind?: string | null): string {
  switch ((authKind ?? '').toLowerCase()) {
    case 'oauth_user':
      return '用户授权(OAuth)';
    case 'token':
    case 'api_key':
      return '访问令牌';
    case '':
      return '未知授权方式';
    default:
      return authKind ?? '未知授权方式';
  }
}

/** 卡片/抽屉标题:优先用户自定义名,其次第三方账号,最后 provider 名。 */
export function connectionName(
  connection: ConnectorConnection,
  provider?: ConnectorProvider | null,
): string {
  return (
    connection.display_name ||
    connection.account ||
    provider?.display_name ||
    connection.provider
  );
}

/**
 * 已授予但缺少的 scope(期望 - 已授予);用于提示重新连接补权限。
 * 连接 DTO 不返回期望 scope,优先用 provider 目录里的 manifest scope。
 */
export function missingScopes(
  connection?: ConnectorConnection | null,
  provider?: ConnectorProvider | null,
): string[] {
  if (!connection) return [];
  const expected = provider?.scopes ?? connection.expected_scopes ?? [];
  const granted = new Set(connection.scopes ?? []);
  return expected.filter((scope) => !granted.has(scope));
}

/** 工具清单:连接上快照优先,回落到 provider manifest。 */
export function connectionTools(
  connection?: ConnectorConnection | null,
  provider?: ConnectorProvider | null,
): ConnectorTool[] {
  if (connection?.tools?.length) return connection.tools;
  return provider?.tools ?? [];
}

/* ── 工具风险 ─────────────────────────────────────────────────────────── */

export type ToolRisk = 'read' | 'write' | 'danger';

export const TOOL_RISK_LABELS: Record<ToolRisk, string> = {
  read: '只读',
  write: '写入',
  danger: '危险',
};

export const TOOL_RISK_TONES: Record<ToolRisk, Tone> = {
  read: 'neutral',
  write: 'warning',
  danger: 'danger',
};

/** 后端 risk 可能是大写枚举或别名词,统一归一到三档。 */
export function toolRisk(risk?: string | null): ToolRisk | null {
  switch ((risk ?? '').toLowerCase()) {
    case 'read':
    case 'readonly':
    case 'safe':
      return 'read';
    case 'write':
    case 'mutate':
      return 'write';
    case 'danger':
    case 'destructive':
    case 'admin':
      return 'danger';
    default:
      return null;
  }
}

/* ── 卡片分组 ─────────────────────────────────────────────────────────── */

export type ConnectorCardData = {
  provider: ConnectorProvider;
  connection: ConnectorConnection | null;
  state: ConnectionState;
  /** 同一 provider 除主连接外的其他连接数(卡片上提示,避免"看不见的连接") */
  extraConnections: number;
};

/** 同 provider 多条连接时优先展示可用且启用的那条。 */
function connectionRank(connection: ConnectorConnection): number {
  if (connection.status === 'active') {
    return connection.enabled === false ? 1 : 0;
  }
  return 2;
}

/**
 * 按 provider 一张卡:目录里的 provider 全部出现(未连接也展示授权入口);
 * 目录里已下架但用户仍有连接时补一张卡,保证连接可管理/可解除。
 */
export function buildConnectorCards(
  providers: ConnectorProvider[],
  connections: ConnectorConnection[],
): ConnectorCardData[] {
  const cards: ConnectorCardData[] = providers.map((provider) => {
    const matched = connections
      .filter((connection) => connection.provider === provider.provider)
      .sort((left, right) => connectionRank(left) - connectionRank(right));
    const connection = matched[0] ?? null;
    return {
      provider,
      connection,
      state: connectionState(connection),
      extraConnections: Math.max(matched.length - 1, 0),
    };
  });

  const known = new Set(providers.map((provider) => provider.provider));
  for (const connection of connections) {
    if (known.has(connection.provider)) continue;
    known.add(connection.provider);
    cards.push({
      provider: {
        provider: connection.provider,
        display_name: connection.provider,
        description: '该 provider 已不在后端目录中,仍可管理或解除这条连接。',
        auth_kind: connection.auth_kind ?? '',
        scopes: connection.expected_scopes ?? [],
        tools: connection.tools ?? [],
      },
      connection,
      state: connectionState(connection),
      extraConnections: 0,
    });
  }
  return cards;
}

/* ── OAuth 回调 ───────────────────────────────────────────────────────── */

const CALLBACK_ERROR_MESSAGES: Record<string, string> = {
  access_denied: '你在授权页取消了授权。',
  state_mismatch: '授权回调校验失败,请重新发起连接。',
  'connector.auth_denied': '授权被拒绝。',
  'connector.scope_missing': '授权缺少必要权限,请重新连接并同意全部 scope。',
};

/** 回调 ?code= 的失败文案;未知错误码保留原文便于排查。 */
export function callbackErrorMessage(code?: string | null): string {
  if (!code) return '授权未完成,请重试。';
  return CALLBACK_ERROR_MESSAGES[code] ?? `授权失败(${code})。`;
}
