# 本地开发

面向「本地优先」开发:不需要 Docker、云 PG/Redis、Vercel 或真实 OAuth 凭据。
本地默认使用 SQLite + 文件系统产物 + LLM 桩。

## 1. 前置

- Node.js >= 20.19
- Python 3.12
- uv >= 0.5
- 首次使用先安装 JS 依赖:

```sh
npm install
```

各 Python 服务的 .env 由启动脚本自动从 .env.example 复制;需要自定义时再手动编辑
(services/\*/.env 已被 gitignore)。

## 2. 一键启动

```sh
npm run dev            # 六个业务服务 + BFF + Vite SPA
npm run dev:site       # 额外启动 Next.js 官网(:3000)
npm run dev:status     # 只检查健康状态
```

脚本行为:

- 启动顺序并行;已有端口被占用则跳过并提示(不会杀已有进程);
- 缺 .env 自动创建;
- 服务日志在 .cache/dev/\*.log;
- Ctrl+C 停止由脚本启动的全部进程;
- 任一子进程退出时整体退出,方便发现崩溃。

## 3. 端口

| 服务               | 端口 | 健康检查    |
| ------------------ | ---- | ----------- |
| account-service    | 8001 | /healthz    |
| agent-orchestrator | 8002 | /healthz    |
| llm-gateway        | 8003 | /healthz    |
| connector-service  | 8004 | /healthz    |
| workspace-service  | 8005 | /healthz    |
| knowledge-service  | 8006 | /healthz    |
| bff                | 8007 | /api/health |
| web                | 5173 | /           |
| site(可选)         | 3000 | /           |

打开 http://localhost:5173。

**BFF(:8007)是必需的**:Vite 的 /api 已统一代理到 BFF,BFF 再转发
orchestrator/account/workspace/knowledge。忘记启动 BFF 时前端会表现为 401/502。

## 4. 鉴权模式

本地默认 VITE_AUTH_MODE=token:

- 登录走 account-service 邮箱+密码;
- 业务请求走 /api → BFF(Bearer 直通兜底);
- 不依赖 Google/GitHub 凭据。

需要验证 Cookie 会话时,把 apps/web/.env.local 设为:

```env
VITE_AUTH_MODE=session
```

然后重启 Vite。session 模式下 BFF 会写加密 httpOnly Cookie,SPA 不接触 token。
OAuth 按钮只有在 account-service 配了 client id/secret 时才可用;本地不配也可用
邮箱+密码登录。

## 3.2 连接器（邮箱）

- 页面：`/connectors`，支持添加 IMAP 连接、测试连通性、读取最近 10 封邮件、SMTP 发信、撤销连接。
- 凭证只提交一次，服务端用 `CONNECTOR__CREDENTIAL_SECRET` 做 Fernet 加密后存 DB，读端永不返回。
- 发信/撤销是写操作，前端二次确认；发信需要连接里填了 SMTP host。
- 本地没有真实邮箱时可以先建一条假连接验证 UI 与加密存储，连通性测试会如实失败。

## 4.0 Job worker（run 队列）

orchestrator 默认开启本地 worker(`AGENT_ORCHESTRATOR__JOBS_ENABLED=true`):

- run 的 plan/approve/confirm/resume/retry 先写 `jobs` 表,worker 轮询领取;
- 领取后置 running + lease(默认 60s),进程崩溃后 lease 过期会被重新领取;
- 失败按 attempts 指数退避,超过 `max_attempts` 落 failed 并记录 last_error;
- 测试夹具显式 `jobs_enabled=false`,保留同步语义。

前端提交审批后会短暂看到原状态,worker 执行完下一次轮询即更新;这是队列化的预期行为。

## 4.1 用量 / 模型 / BYOK

- `/usage`：今日/本月 tokens、请求次数、按模型分布；没有数据时显示空态。
- 顶栏模型 chip 读取 llm-gateway 的 `/v1/models`（当前默认 `deepseek/deepseek-chat`）。
- 设置页 BYOK：应用层加密存储，只显示尾号；**暂不参与模型调用**。
- 本地没有模型 key 时用量页为空是正常现象；可用 account-service 的内部上报接口造数：

¤¤¤sh

# 需要 ACCOUNT\_\_INTERNAL_SECRET

curl -X POST http://127.0.0.1:8001/v1/usage/events \
 -H "Authorization: Bearer <access-token>" \
 -H "X-Internal-Secret: <internal-secret>" \
 -H 'Content-Type: application/json' \
 -d '{"model":"deepseek/deepseek-chat","prompt_tokens":1200,"completion_tokens":400}'
¤¤¤

## 5. 演示数据

```sh
npm run dev:seed
```

会创建/复用一个本地演示账号,并写入:

- 2 个会话
- Markdown / Diff / 图片产物
- 1 个演示 run(LLM 关闭时走确定性桩)

脚本是幂等的:同标题会话/产物已存在会跳过。

默认账号:

```text
demo@example.com / DemoPass123!
```

可用环境变量覆盖:SEED_EMAIL、SEED_PASSWORD。

## 6. 重置本地数据

```sh
npm run dev:reset          # 交互确认
npm run dev:reset -- --yes # 不确认
```

会删除:

- services/_/_.db(SQLite)
- services/\*/.artifacts(本地产物)
- .cache/dev(日志)

保留 .env 与源码。服务运行中会拒绝删除;先 Ctrl+C 停 dev.sh,或加 --force。
重置后重新 npm run dev,服务会在 AUTO_CREATE_TABLES=true 时自动建表。

## 7. 日志与排障

| 现象                | 处理                                                                          |
| ------------------- | ----------------------------------------------------------------------------- |
| 端口被占用          | scripts/dev.sh 会跳过;确认是否已有旧进程,必要时手动 kill                      |
| 前端 401/502        | 检查 BFF :8007 是否启动;npm run dev:status                                    |
| 登录后页面空        | 检查 account-service :8001 与 BFF 日志                                        |
| 会话接口 404/旧结构 | 重启 agent-orchestrator,确认它跑的是当前代码                                  |
| 官网打不开          | 单独 npm run dev:site(SPA 里「官网」跳 :3000)                                 |
| SQLite 表结构不对   | npm run dev:reset 后重启                                                      |
| 想用真实模型        | 配 llm-gateway 的 LLM_ENABLED/API_KEY,或用本地 OpenAI 兼容端点                |
| 附件上传失败        | 检查大小 ≤10MB、类型(文本/Markdown/CSV/JSON/PDF/图片)与 orchestrator 是否最新 |

## 8. 测试

```sh
npx nx test @beauty/web -- --run
npx nx test bff
npx nx test account-service
npx nx test agent-orchestrator
npx nx typecheck @beauty/web
npx nx lint @beauty/web
```

注意:不带 -- --run 的 npx nx test 是 vitest watch 模式,会挂住。

## 9. 暂不包含

- 容器化/云 PG/云 Redis/部署
- 真实 Google/GitHub OAuth 凭据
- Sentry、对象存储、沙箱执行、MCP
