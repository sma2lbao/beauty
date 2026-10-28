/**
 * 可选模型目录数据层(C1 模型路由,契约见 docs/design/model-routing.md §6.3)。
 *
 * `GET /api/v1/models` 由 agent-orchestrator 提供,数据来自 llm-gateway 的模型清单
 * (网关不可达时回退服务端配置,再不可用就是空列表——所以空数组是合法结果,不是错误)。
 * 与 legacy `insightsApi.models`(BFF 同源 `/api/models`,返回 `{object,default_model,data}`)
 * 是两条线:这里的响应没有 W3C `object` 字段,前端只用 `id` / `owned_by`。
 *
 * 前端只负责「展示可选模型 + 把用户选的 id 带进请求」;白名单校验在网关,
 * 不在清单里时后端返回 400 `llm.model_not_allowed`,走既有错误通道展示。
 */

import { createHttpClient } from '@/lib/http';
import { runsBase as BASE } from '@/config/env';

/** 模型条目:`id` 是请求里要带的模型名,`owned_by` 用于分组/归属展示。 */
export type ModelInfo = {
  id: string;
  owned_by: string;
};

/** 模型目录:`default_model` 为 null 表示服务端没有配置默认模型(交给网关默认路由)。 */
export type ModelsResponse = {
  default_model: string | null;
  models: ModelInfo[];
};

const http = createHttpClient(BASE);

export const modelsApi = {
  listModels: () => http.request<ModelsResponse>('/api/v1/models'),
};
