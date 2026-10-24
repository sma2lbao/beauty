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
import { createHttpClient } from '@/lib/http';
import { knowledgeBase as BASE } from '@/config/env';

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

const http = createHttpClient(BASE);

export const knowledgeApi = {
  documents: () =>
    http.request<KnowledgeDocument[]>(`${PREFIX}/v1/knowledge/documents`),
  ingest: (body: { title: string; content: string; source?: string }) =>
    http.request<KnowledgeDocument>(`${PREFIX}/v1/knowledge/documents`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  remove: (documentId: string) =>
    http.request<void>(`${PREFIX}/v1/knowledge/documents/${documentId}`, {
      method: 'DELETE',
    }),
  search: (query: string, limit = 5) =>
    http.request<SearchResult>(`${PREFIX}/v1/knowledge/search`, {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    }),
};
