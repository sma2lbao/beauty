/**
 * 记忆控制台的数据层:agent-orchestrator 的 /api/v1/memories 契约。
 *
 * 契约来源 services/agent-orchestrator/agent_orchestrator/api/v1/memory.py:
 * - 列表 `GET /api/v1/memories`(workspace_id/kind/source_type/q/include_invalid/limit);
 * - 单条 `GET /api/v1/memories/{id}`(含已失效,便于查看替代链);
 * - 语义检索 `GET /api/v1/memories/search`(query 必填,只读不计数);
 * - 统计 `GET /api/v1/memories/stats`(total/active/pinned/expired/superseded/pending_reviews);
 * - 新建 `POST /api/v1/memories`(201 返回 MemoryRead);
 * - 局部更新 `PATCH /api/v1/memories/{id}`(expected_updated_at 乐观并发,冲突 409);
 * - 删除 `DELETE /api/v1/memories/{id}`(204,跨账号 404 memory.not_found);
 * - 清理过期 `POST /api/v1/memories/purge-expired`(返回 purged 条数);
 * - 复核队列 `GET /api/v1/memories/reviews` + approve/reject;
 * - 导出 `GET /api/v1/memories/export`;彻底删除 `DELETE /api/v1/memories?confirm=true`。
 *
 * 基址与 runs/outbox 相同(`VITE_RUNS_API_URL`,留空走同源 API 网关代理),
 * 鉴权头/401 续期/错误映射全部复用 `@/lib/http`。
 */

import { runsBase as BASE } from '@/config/env';
import { createHttpClient } from '@/lib/http';

const http = createHttpClient(BASE);

const MEMORIES_PATH = '/api/v1/memories';

/** 记忆类型(与后端 MemoryKind 字面量一致)。 */
export const MEMORY_KINDS = [
  'note',
  'preference',
  'fact',
  'procedure',
] as const;
export type MemoryKind = (typeof MEMORY_KINDS)[number];

/** 记忆作用域:account 账号级 / workspace 工作区级。 */
export const MEMORY_SCOPES = ['account', 'workspace'] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

/** 注入模式:retrieved 按相关性召回 / always 常驻核心块。 */
export const MEMORY_INJECTION_MODES = ['retrieved', 'always'] as const;
export type MemoryInjectionMode = (typeof MEMORY_INJECTION_MODES)[number];

/** 记忆来源(与后端 MEMORY_SOURCE_TYPES 一致)。 */
export const MEMORY_SOURCES = ['manual', 'chat', 'run', 'tool'] as const;
export type MemorySource = (typeof MEMORY_SOURCES)[number];

/** 一条记忆(对应后端 MemoryRead,无 embedding 字段)。 */
export type Memory = {
  id: string;
  workspace_id: string | null;
  scope: MemoryScope;
  kind: MemoryKind;
  content: string;
  pinned: boolean;
  source_type: string;
  source_id: string | null;
  injection_mode: MemoryInjectionMode | string;
  confidence: number;
  valid_from: string | null;
  invalid_at: string | null;
  superseded_by: string | null;
  expires_at: string | null;
  last_accessed_at: string | null;
  access_count: number;
  created_at: string;
  updated_at: string;
};

/** 记忆统计(对应后端 GET /memories/stats)。 */
export type MemoryStats = {
  total: number;
  active: number;
  pinned: number;
  expired: number;
  superseded: number;
  pending_reviews: number;
};

/** 待复核候选(低置信度自动抽取;通过后才写入长期记忆)。 */
export type MemoryReview = {
  id: string;
  workspace_id: string | null;
  scope: MemoryScope | string;
  kind: MemoryKind | string;
  content: string;
  confidence: number;
  evidence_quote: string | null;
  source_type: string;
  source_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

/** 检索响应:命中项就是普通 MemoryRead 列表。 */
export type MemorySearchResponse = {
  query: string;
  hits: Memory[];
};

/** 导出响应:含失效条目,便于迁移/备份。 */
export type MemoryExport = {
  exported_at: string;
  memories: Memory[];
};

export type MemoryListQuery = {
  workspaceId?: string;
  kind?: MemoryKind;
  sourceType?: string;
  q?: string;
  /** 是否包含已被替代/失效的条目。 */
  includeInvalid?: boolean;
  limit?: number;
};

export type MemorySearchQuery = {
  query: string;
  workspaceId?: string;
  kind?: MemoryKind;
  /** 后端上限 20,默认 5。 */
  limit?: number;
};

/** 新建记忆入参:字段名与后端 MemoryCreate 逐字对齐。 */
export type MemoryCreateInput = {
  content: string;
  kind?: MemoryKind;
  scope?: MemoryScope;
  workspace_id?: string | null;
  pinned?: boolean;
  ttl_days?: number | null;
  injection_mode?: MemoryInjectionMode;
};

/** 局部更新入参:字段名与后端 MemoryUpdate 逐字对齐。 */
export type MemoryUpdateInput = {
  content?: string;
  kind?: MemoryKind;
  pinned?: boolean;
  ttl_days?: number | null;
  injection_mode?: MemoryInjectionMode;
  /** 客户端读到的 updated_at:服务端检测到并发修改时返回 409。 */
  expected_updated_at?: string;
};

function toListQuery(query: MemoryListQuery): string {
  const params = new URLSearchParams();
  if (query.workspaceId) params.set('workspace_id', query.workspaceId);
  if (query.kind) params.set('kind', query.kind);
  if (query.sourceType) params.set('source_type', query.sourceType);
  if (query.q) params.set('q', query.q);
  if (query.includeInvalid) params.set('include_invalid', 'true');
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  const search = params.toString();
  return search ? `?${search}` : '';
}

export const memoryApi = {
  list: (query: MemoryListQuery = {}) =>
    http.request<Memory[]>(`${MEMORIES_PATH}${toListQuery(query)}`),
  get: (memoryId: string) =>
    http.request<Memory>(`${MEMORIES_PATH}/${encodeURIComponent(memoryId)}`),
  search: (query: MemorySearchQuery) => {
    const params = new URLSearchParams({ query: query.query });
    if (query.workspaceId) params.set('workspace_id', query.workspaceId);
    if (query.kind) params.set('kind', query.kind);
    if (query.limit !== undefined) params.set('limit', String(query.limit));
    return http.request<MemorySearchResponse>(
      `${MEMORIES_PATH}/search?${params.toString()}`,
    );
  },
  stats: () => http.request<MemoryStats>(`${MEMORIES_PATH}/stats`),
  create: (input: MemoryCreateInput) =>
    http.request<Memory>(MEMORIES_PATH, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  update: (memoryId: string, input: MemoryUpdateInput) =>
    http.request<Memory>(`${MEMORIES_PATH}/${encodeURIComponent(memoryId)}`, {
      method: 'PATCH',
      body: JSON.stringify(input),
    }),
  remove: (memoryId: string) =>
    http.request<void>(`${MEMORIES_PATH}/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
    }),
  purgeExpired: () =>
    http.request<{ purged: number }>(`${MEMORIES_PATH}/purge-expired`, {
      method: 'POST',
    }),
  reviews: (limit = 50) =>
    http.request<MemoryReview[]>(
      `${MEMORIES_PATH}/reviews?limit=${encodeURIComponent(String(limit))}`,
    ),
  approveReview: (reviewId: string, note?: string) =>
    http.request<Memory>(
      `${MEMORIES_PATH}/reviews/${encodeURIComponent(reviewId)}/approve`,
      { method: 'POST', body: JSON.stringify({ note: note ?? null }) },
    ),
  rejectReview: (reviewId: string, note?: string) =>
    http.request<void>(
      `${MEMORIES_PATH}/reviews/${encodeURIComponent(reviewId)}/reject`,
      { method: 'POST', body: JSON.stringify({ note: note ?? null }) },
    ),
  exportAll: () => http.request<MemoryExport>(`${MEMORIES_PATH}/export`),
  removeAll: () =>
    http.request<{ memories: number; reviews: number }>(
      `${MEMORIES_PATH}?confirm=true`,
      { method: 'DELETE' },
    ),
};
