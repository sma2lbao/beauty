# llm-gateway

模型网关(阶段 A 骨架):全平台**唯一的** LLM/embedding 调用出口(架构 §5)。
任何服务不得直连模型提供商——路由、限流、配额、审计、出口检查都收敛在这里。

- `POST /v1/completions`:OpenAI 风格消息 → litellm 统一路由(DeepSeek/OpenAI/…,
  `model` 用 litellm 的 `provider/model` 格式如 `deepseek/deepseek-chat`),
  不传 `model` 走默认路由;失败统一映射 502 `llm.upstream_error`;
- 支持 function-calling:`tools` 原样透传给 litellm,响应的 `tool_calls`
  归一化为 OpenAI 嵌套形状(`function.name/arguments`);消息支持
  `assistant.tool_calls` 与 `tool` 角色(`tool_call_id`),历史回传无需转换;
- `/healthz` `/readyz`:存活/就绪探针(默认模型已配置即就绪,提供商连通性在调用时校验);
- 密钥由 litellm 直接读进程环境变量,网关不落盘(密钥库方案见架构 §10)。

阶段 B 补齐:embeddings 端点、租户级配额与限流、模型路由按区域切换(§9)、
调用审计落库、LiteLLM proxy 配置化(fallback/guardrails)。

命令(仓库根):`npx nx run llm-gateway:serve`(:8003)/ `npx nx test llm-gateway`
