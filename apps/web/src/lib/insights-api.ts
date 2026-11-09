/**
 * 用量/模型/BYOK 数据层(M0-11 本地版)。
 *
 * 全部走 API 网关同源: /api/models、/api/usage/summary、/api/byok
 * (API 网关再转发 llm-gateway / account-service;token 模式 Bearer 兜底)。
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
  /** 该模型本月估算成本(USD);未计价调用按 0 计入,见 unpriced_events */
  cost_usd: number;
  /** 该模型本月未计价调用条数(价目表缺项/BYOK 自定义模型) */
  unpriced_events: number;
};

export type UsageSummary = {
  today_tokens: number;
  month_tokens: number;
  today_events: number;
  month_events: number;
  /** 网关按价目表估算的成本(USD);未计价调用按 0 计,另见 unpriced_events */
  today_cost_usd: number;
  month_cost_usd: number;
  /** 本月未计价调用条数:金额为 0 不一定是真免费 */
  unpriced_events: number;
  by_model: UsageByModel[];
};

export type BYOKKey = {
  provider: string;
  masked_key: string;
  key_hint: string;
  updated_at: string;
  /** 该 provider 下的自定义模型 id(每项以 `<provider>/` 开头)。 */
  models: string[];
};

const http = createHttpClient('');

export const insightsApi = {
  models: () => http.request<ModelList>('/api/models'),
  usage: () => http.request<UsageSummary>('/api/usage/summary'),
  byok: () => http.request<BYOKKey[]>('/api/byok'),
  /**
   * 保存/更新某个 provider 的 BYOK 配置。
   *
   * `apiKey` 为 null 或空串表示"不更换密钥"(该 provider 已有记录时合法);
   * `models` 是自定义模型清单,整体替换(空数组 = 清空)。
   */
  saveByok: (provider: string, apiKey: string | null, models: string[]) =>
    http.request<BYOKKey>(`/api/byok/${provider}`, {
      method: 'PUT',
      body: JSON.stringify({
        ...(apiKey ? { api_key: apiKey } : {}),
        models,
      }),
    }),
  deleteByok: (provider: string) =>
    http.request<void>(`/api/byok/${provider}`, { method: 'DELETE' }),
};
