/**
 * 应用级 hooks 数据层(P1-B,design/hooks.md §8):账号级 webhook 挂点增删改查。
 *
 * 读端永不回显 secret——它只在创建与轮换响应里出现一次;更新只允许局部字段
 * (matcher/target/timeout_seconds/enabled),事件不可改。服务在 agent-orchestrator,
 * 沿用 /api/v1 前缀,错误由 @/lib/http 统一抛 ApiError(code 见 §7)。
 */
import { createHttpClient } from '@/lib/http';
import { runsBase as BASE } from '@/config/env';

/** 五个生命周期挂点(与后端 schemas/hook.py 的枚举一致)。 */
export type HookEvent =
  | 'PreToolUse'
  | 'PostToolUse'
  | 'SessionStart'
  | 'SessionEnd'
  | 'PreCompact';

export const HOOK_EVENTS: HookEvent[] = [
  'PreToolUse',
  'PostToolUse',
  'SessionStart',
  'SessionEnd',
  'PreCompact',
];

export type Hook = {
  id: string;
  event: HookEvent;
  matcher: string;
  kind: string;
  target: string;
  timeout_seconds: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

/** 创建/轮换的响应:额外一次性返回 secret(接收端据此校验签名)。 */
export type HookSecret = Hook & { secret: string };

export type HookCreateInput = {
  event: HookEvent;
  matcher?: string;
  target: string;
  timeout_seconds?: number;
  enabled?: boolean;
};

export type HookUpdateInput = {
  matcher?: string;
  target?: string;
  timeout_seconds?: number;
  enabled?: boolean;
};

const http = createHttpClient(BASE);

export const hooksApi = {
  /** 列表(created_at, id 升序;不含 secret)。 */
  list: async () => http.request<Hook[]>('/api/v1/hooks'),
  create: async (input: HookCreateInput) =>
    http.request<HookSecret>('/api/v1/hooks', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  update: async (hookId: string, patch: HookUpdateInput) =>
    http.request<Hook>(`/api/v1/hooks/${hookId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  remove: async (hookId: string) =>
    http.request<void>(`/api/v1/hooks/${hookId}`, { method: 'DELETE' }),
  /** 轮换签名密钥:返回新 secret,旧 secret 立即失效。 */
  rotate: async (hookId: string) =>
    http.request<HookSecret>(`/api/v1/hooks/${hookId}/rotate`, {
      method: 'POST',
    }),
};
