/**
 * 管理台(P0-7)的数据层:account-service 的 admin 契约。
 *
 * 契约唯一真源是 docs/design/admin-plane.md §3.3:权限由服务端
 * require_admin 每请求查库校验(角色不进 JWT),前端只做展示与转发。
 *
 * 基址/鉴权与账号域一致(见 lib/account-api.ts):session 模式走同源
 * /api/account(API 网关),token 模式走 /account-api(account-service)。
 */
import { accountBase as BASE } from '@/config/env';
import { authMode } from '@/lib/auth';
import { createHttpClient } from '@/lib/http';
import type { UsageSummary } from '@/lib/insights-api';

const PREFIX = authMode() === 'session' ? '/api/account' : '/account-api';

/** 平台角色:普通用户 / 平台管理员。 */
export type AdminRole = 'user' | 'admin';
export type AdminStatus = 'active' | 'disabled';

/** 本自然月(UTC)的跨账号用量投影。 */
export type AdminUsage = {
  month_tokens: number;
  month_events: number;
  month_cost_usd: number;
  /** 没有计价记录的调用次数:金额按 0 计,单列以免看起来"少算了"。 */
  unpriced_events: number;
};

/** 配额原样透传:字段缺省或为 null 都表示不限。 */
export type AdminQuota = {
  monthly_tokens?: number | null;
  max_concurrent_runs?: number | null;
};

export type AdminAccount = {
  id: string;
  email: string;
  display_name: string | null;
  status: AdminStatus;
  role: AdminRole;
  quota: AdminQuota;
  created_at: string;
  usage: AdminUsage;
};

export type AdminAccountList = {
  items: AdminAccount[];
  total: number;
};

/** 单账号详情:usage 是完整 UsageSummary(列表只带月度投影)。 */
export type AdminAccountDetail = {
  account: AdminAccount;
  usage: UsageSummary;
};

export type AuditActionStatus = 'success' | 'denied' | 'failed';

export type AuditLogRead = {
  id: string;
  actor_account_id: string;
  actor_email: string;
  action: string;
  target_type: 'account' | 'endpoint';
  target_id: string;
  status: AuditActionStatus;
  detail: Record<string, unknown>;
  request_id: string | null;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
};

export type AuditLogList = {
  items: AuditLogRead[];
  total: number;
};

/** PATCH quota:省略字段=保持,显式 null=清除(不限),整数 >= 1。 */
export type QuotaPatch = {
  monthly_tokens?: number | null;
  max_concurrent_runs?: number | null;
};

export type AdminAccountQuery = {
  q?: string;
  status?: AdminStatus;
  limit?: number;
  offset?: number;
};

export type AdminAuditQuery = {
  limit?: number;
  offset?: number;
  actor_account_id?: string;
  action?: string;
  target_id?: string;
};

type QueryValue = string | number | undefined;

/** 跳过 undefined/空串:空过滤条件不该以 ?action= 的形式落到服务端。 */
function withQuery(path: string, params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') {
      continue;
    }
    search.set(key, String(value));
  }
  const serialized = search.toString();
  return serialized ? path + '?' + serialized : path;
}

const http = createHttpClient(BASE);

export const adminApi = {
  /** 跨账号清单:q 对 email/display_name 做大小写不敏感包含匹配。 */
  listAccounts: (query: AdminAccountQuery = {}) =>
    http.request<AdminAccountList>(
      withQuery(PREFIX + '/v1/admin/accounts', {
        q: query.q,
        status: query.status,
        limit: query.limit,
        offset: query.offset,
      }),
    ),
  getAccount: (accountId: string) =>
    http.request<AdminAccountDetail>(
      PREFIX + '/v1/admin/accounts/' + accountId,
    ),
  updateQuota: (accountId: string, patch: QuotaPatch) =>
    http.request<AdminAccount>(
      PREFIX + '/v1/admin/accounts/' + accountId + '/quota',
      { method: 'PATCH', body: JSON.stringify(patch) },
    ),
  updateRole: (accountId: string, body: { role: AdminRole }) =>
    http.request<AdminAccount>(
      PREFIX + '/v1/admin/accounts/' + accountId + '/role',
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
  updateStatus: (accountId: string, body: { status: AdminStatus }) =>
    http.request<AdminAccount>(
      PREFIX + '/v1/admin/accounts/' + accountId + '/status',
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
  /** 审计日志:服务端按 created_at desc, id desc 排序;过滤为精确匹配。 */
  listAuditLogs: (query: AdminAuditQuery = {}) =>
    http.request<AuditLogList>(
      withQuery(PREFIX + '/v1/admin/audit-logs', {
        limit: query.limit,
        offset: query.offset,
        actor_account_id: query.actor_account_id,
        action: query.action,
        target_id: query.target_id,
      }),
    ),
};
