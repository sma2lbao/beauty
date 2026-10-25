/**
 * 记忆控制台的数据层:agent-orchestrator 的 /api/v1/memories 契约。
 *
 * 契约来源 services/agent-orchestrator/agent_orchestrator/api/v1/memory.py:
 * - 列表 `GET /api/v1/memories`(workspace_id/kind/q/limit 过滤);
 * - 语义检索 `GET /api/v1/memories/search`(query 必填);
 * - 统计 `GET /api/v1/memories/stats`(total/active/pinned/expired);
 * - 新建 `POST /api/v1/memories`(201 返回 MemoryRead);
 * - 删除 `DELETE /api/v1/memories/{id}`(204,跨租户 404 memory.not_found);
 * - 清理过期 `POST /api/v1/memories/purge-expired`(返回 purged 条数)。
 *
 * 基址与 runs/outbox 相同(`VITE_RUNS_API_URL`,留空走同源 BFF 代理),
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

/** 记忆作用域:tenant 租户级 / workspace 工作区级。 */
export const MEMORY_SCOPES = ['tenant', 'workspace'] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

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
};

/** 检索响应:命中项就是普通 MemoryRead 列表。 */
export type MemorySearchResponse = {
  query: string;
  hits: Memory[];
};

export type MemoryListQuery = {
  workspaceId?: string;
  kind?: MemoryKind;
  q?: string;
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
};

function toListQuery(query: MemoryListQuery): string {
  const params = new URLSearchParams();
  if (query.workspaceId) params.set('workspace_id', query.workspaceId);
  if (query.kind) params.set('kind', query.kind);
  if (query.q) params.set('q', query.q);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  const search = params.toString();
  return search ? `?${search}` : '';
}

export const memoryApi = {
  list: (query: MemoryListQuery = {}) =>
    http.request<Memory[]>(`${MEMORIES_PATH}${toListQuery(query)}`),
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
  remove: (memoryId: string) =>
    http.request<void>(`${MEMORIES_PATH}/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
    }),
  purgeExpired: () =>
    http.request<{ purged: number }>(`${MEMORIES_PATH}/purge-expired`, {
      method: 'POST',
    }),
};
