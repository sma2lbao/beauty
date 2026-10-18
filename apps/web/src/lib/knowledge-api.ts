/**
 * 知识库控制台的数据层:knowledge-service 的 API 契约(架构 §9)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_KNOWLEDGE_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/knowledge-api` 代理到 :8006,见 vite.config.ts);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时租户头 / `token` / `logto` Bearer),
 *   与运行控制台共用同一套。
 */

import { authMode } from '@/lib/auth';
import { requestJson } from '@/lib/http';

const BASE =
  (import.meta.env.VITE_KNOWLEDGE_API_URL as string | undefined) ?? '';

const PREFIX = authMode() === 'session' ? '/api/knowledge' : '/knowledge-api';

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

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(BASE, `${PREFIX}${path}`, init);
}

export const knowledgeApi = {
  documents: () => request<KnowledgeDocument[]>('/v1/knowledge/documents'),
  ingest: (body: { title: string; content: string; source?: string }) =>
    request<KnowledgeDocument>('/v1/knowledge/documents', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  remove: (documentId: string) =>
    request<void>(`/v1/knowledge/documents/${documentId}`, {
      method: 'DELETE',
    }),
  search: (query: string, limit = 5) =>
    request<SearchResult>('/v1/knowledge/search', {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    }),
};
