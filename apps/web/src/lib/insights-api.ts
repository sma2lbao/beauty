/**
 * 用量/模型/BYOK 数据层(M0-11 本地版)。
 *
 * 全部走 BFF 同源: /api/models、/api/usage/summary、/api/byok
 * (BFF 再转发 llm-gateway / account-service;token 模式 Bearer 兜底)。
 */
import { createHttpClient } from '@/lib/http';

export type ModelInfo = {
  id: string;
  object: string;
  owned_by?: string;
};

export type ModelList = {
  object: string;
  default_model: string;
  data: ModelInfo[];
};

export type UsageByModel = {
  model: string;
  tokens: number;
  events: number;
};

export type UsageSummary = {
  today_tokens: number;
  month_tokens: number;
  today_events: number;
  month_events: number;
  by_model: UsageByModel[];
};

export type BYOKKey = {
  provider: string;
  masked_key: string;
  key_hint: string;
  updated_at: string;
};

const http = createHttpClient('');

export const insightsApi = {
  models: () => http.request<ModelList>('/api/models'),
  usage: () => http.request<UsageSummary>('/api/usage/summary'),
  byok: () => http.request<BYOKKey[]>('/api/byok'),
  saveByok: (provider: string, apiKey: string) =>
    http.request<BYOKKey>(`/api/byok/${provider}`, {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteByok: (provider: string) =>
    http.request<void>(`/api/byok/${provider}`, { method: 'DELETE' }),
};
