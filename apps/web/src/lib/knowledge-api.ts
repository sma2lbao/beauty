/**
 * 知识库控制台的数据层:knowledge-service 的 API 契约(架构 §9)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_KNOWLEDGE_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/knowledge-api` 代理到 :8006,见 vite.config.ts);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时账号头 / `token` / `session`),
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

export type SearchMode = 'vector' | 'keyword' | 'hybrid';

/**
 * 重排状态:applied = 顺序与分数都来自重排模型;skipped = 网关不可用已降级,
 * 仍是粗排顺序;null / 缺省 = 该部署没开重排。三种情况分数口径不同。
 */
export type RerankStatus = 'applied' | 'skipped';

export type SearchResult = {
  query: string;
  provider: string;
  model: string;
  /** 实际检索模式,服务端回传(旧版本可能没有)。 */
  mode?: SearchMode;
  /** 服务端检索耗时(毫秒)。 */
  elapsed_ms?: number;
  /** 重排状态;生效时 `score` 改为重排相关性分数(0~1)。 */
  rerank?: RerankStatus | null;
  /** 生效/降级时使用的重排模型(哪套模型参与了排序)。 */
  rerank_model?: string | null;
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
