/**
 * 知识库控制台的数据层:knowledge-service 的 API 契约(架构 §9)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_KNOWLEDGE_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/knowledge-api` 代理到 :8006,见 vite.config.ts);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时租户头 / `token` / `logto` Bearer),
 *   与运行控制台共用同一套。
 */

import { ApiError } from '@/lib/runs-api';
import { authHeaders } from '@/lib/auth';

const BASE = (import.meta.env.VITE_KNOWLEDGE_API_URL as string | undefined) ?? '';

const PREFIX = '/knowledge-api';

export type KnowledgeDocument = {
  id: string;
  title: string;
  source: string;
  size_bytes: number;
  chunk_count: number;
  embedding_model: string;
  created_at: string;
};

export type SearchHit = {
  chunk_id: string;
  document_id: string;
  title: string;
  ordinal: number;
  content: string;
  score: number;
};

export type SearchResult = {
  query: string;
  provider: string;
  model: string;
  hits: SearchHit[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${PREFIX}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(await authHeaders()),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let code = 'request_failed';
    let message = `请求失败(${response.status})`;
    try {
      const body = (await response.json()) as { code?: string; message?: string };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      // 非 JSON 错误体:保留兜底文案
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const knowledgeApi = {
  documents: () => request<KnowledgeDocument[]>('/v1/knowledge/documents'),
  ingest: (body: { title: string; content: string; source?: string }) =>
    request<KnowledgeDocument>('/v1/knowledge/documents', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  remove: (documentId: string) =>
    request<void>(`/v1/knowledge/documents/${documentId}`, { method: 'DELETE' }),
  search: (query: string, limit = 5) =>
    request<SearchResult>('/v1/knowledge/search', {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    }),
};
