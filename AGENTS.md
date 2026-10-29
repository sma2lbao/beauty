# AGENTS.md

**beauty-moon** 是基于 [Nx](https://nx.dev) 的 polyglot monorepo:JS/TS 走 npm workspaces(`packages/*`、`apps/*`),Python 走 [uv](https://docs.astral.sh/uv/) workspace(经 [@nxlv/python](https://github.com/lucasvieirasilva/nx-plugins) 插件编排)。所有命令默认在仓库根目录执行。

## 项目结构

| 路径                          | Nx 项目名               | 类型              | 技术栈                                                                                                                                                                                  |
| ----------------------------- | ----------------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/web`                    | `@beauty/web`           | React 应用        | React 19 + react-router v8 + @tanstack/react-query + Tailwind CSS v4 + `@beauty/design` 组件库,Vite 8                                                                                   |
| `apps/site`                   | `@beauty/site`          | Next.js 官网      | Next.js App Router + Tailwind CSS v4 + `@beauty/design` 令牌;静态导出(`output: 'export'`),部署 Vercel                                                                                   |
| `apps/desktop`                | `@beauty/desktop`       | Electron 桌面应用 | Electron 44 + React 19 + Tailwind CSS v4 + `@beauty/design`;**本地电脑模式**:会话/文件/命令都在本机执行,主进程运行时在 `src/main/local/`,与 `apps/web` 云端模式并存可切换;详见其 README |
| `packages/design`             | `@beauty/design`        | React 组件库      | 纯 CSS + `bd-` 前缀 BEM 类名 + `--bd-*` 设计令牌;rspress 文档站                                                                                                                         |
| `packages/common`             | `common`                | Python 库         | 服务基础设施:统一错误模型、配置基类、结构化日志、JWT 鉴权基座(JWKS)、交付物存储抽象;hatchling + pytest + ruff                                                                           |
| `packages/database`           | `database`              | Python 库         | 数据库基座:命名约定、Base/mixin、session_scope、Repository(租户隔离+统一 404)、UnitOfWork、Postgres 集成夹具;hatchling + pytest + ruff                                                  |
| `packages/events`             | `events`                | Python 库         | 事件信封(含租户)、进程内总线、事务性 outbox(仓储/dispatcher)、消费幂等;hatchling + pytest + ruff                                                                                        |
| `services/agent-orchestrator` | `agent-orchestrator`    | Python 服务       | FastAPI + uvicorn(:8002):run 编排/事件溯源/LangGraph 执行图(阶段 A)                                                                                                                     |
| `services/llm-gateway`        | `llm-gateway`           | Python 服务       | FastAPI + litellm(:8003):全平台唯一模型出口,统一路由与 502 错误契约                                                                                                                     |
| `services/connector-service`  | `connector-service`     | Python 服务       | FastAPI + uvicorn(:8004):连接器框架(当前无内置连接器:保留健康探针、连接列表/详情/撤销与凭证加密;邮件与日历能力均已下线)                                                                 |
| `services/knowledge-service`  | `knowledge-service`     | Python 服务       | FastAPI + uvicorn(:8006):文档知识库(分块/嵌入/检索;嵌入提供方可插拔)                                                                                                                    |
| `services/account-service`    | `account-service`       | Python 服务       | FastAPI + uvicorn(:8001):用户/组织/配额/套餐 + sessions/devices(自建认证:注册/邮箱密码登录/旋转式刷新令牌/内部签发 + OIDC 发现/JWKS,签名密钥可经 `SIGNING_KEY_PATH` 持久化,见其 README) |
| `services/workspace-service`  | `workspace-service`     | Python 服务       | FastAPI + uvicorn(:8005):工作区/交付物元数据/对象存储(沙箱生命周期阶段 B)                                                                                                               |
| `services/tool-service`       | `tool-service`          | Python 服务       | FastAPI + uvicorn(:8008):内置工具服务(catalog/执行/审计/ask_question;沙箱经 workspace-service 创建);设计见 `docs/design/tool-service.md`                                                |
| `services/bff`                | `bff`                   | Python 服务       | FastAPI + uvicorn(:8007):同源 `/api` 门面;M0-05/06 承载 Cookie 会话、代理、限流、下载、stream token                                                                                     |
| `tools/eslint-config`         | `@beauty/eslint-config` | 共享工具包        | ESLint 9 flat config(`base`/`react` 工厂);eslint 插件依赖收敛于此                                                                                                                       |

微服务架构设计(定稿)见 `docs/architecture.md`:微服务放 `services/`(部署单元),共享库实名命名(不用 `py-` 前缀),新 Python 项目必须在根 `pyproject.toml` 登记 uv workspace members。

环境要求:Node.js >= 22.22.2(jsdom 30 与 undici 8 的下限);Python 3.12(根 `.python-version` 锁定);uv >= 0.5。

## 常用命令

### 全仓库

```sh
npx nx show projects        # 列出所有项目
npx nx run-many -t lint     # 全仓库 lint(JS/TS 用 ESLint,Python 用 ruff)
npx nx run-many -t build    # 构建所有可构建项目
npx nx graph                # 打开项目依赖关系图

npm run dev                 # 本地一键启动(七服务 + BFF + web;详见 docs/local-dev.md)
npm run dev:status          # 本地健康检查
npm run dev:migrate         # 本地 SQLite 迁移到 alembic head(dev 启动前自动执行)
npm run dev:seed            # 写入演示数据
npm run dev:reset           # 清空本地 SQLite/产物
```

### TypeScript / React

`nx.json` 配置了 `testMode: "watch"`,因此 **`npx nx test <项目>` 是 vitest watch 模式,会挂住不退出**。脚本/agent 场景务必追加 `-- --run`:

```sh
npx nx test @beauty/web -- --run       # 一次性运行测试(推荐)
npx nx test @beauty/design -- --run

npx nx dev @beauty/web                 # Vite dev server(5173 端口,交互开发用)
npx nx build @beauty/web               # tsc --noEmit + vite 生产构建
npx nx run @beauty/site:dev            # Next.js 官网 dev(3000 端口)
npx nx build @beauty/site              # Next.js 静态导出(产物 apps/site/out)
npx nx typecheck @beauty/web           # 仅类型检查(web/design 同名)
npx nx lint @beauty/web                # ESLint(共享配置 tools/eslint-config)
npx nx lint @beauty/web -- --fix       # ESLint 自动修复
npx nx build @beauty/design            # tsc emit ESM 到 dist
npx nx run @beauty/design:docs:dev     # rspress 文档站
```

注意:`test-ci` target 是 atomized task,本地运行要求 Nx Cloud,请勿使用。

### 质量与发布门禁(重构后新增,W0–W4)

```sh
bash scripts/refactor-gate.sh @beauty/web   # 单项目全门禁:lint/typecheck/test/build(+覆盖率)/docs
bash scripts/refactor-gate.sh --affected    # 只跑受影响项目(不收集覆盖率)
bash scripts/check-docs.sh                  # docs prettier + 相对链接/锚点
bash scripts/check-workspace.sh             # 未跟踪产物/归档注记/索引覆盖三查
bash scripts/check-versions.sh              # 19 处版本字段 + CHANGELOG + release 配置一致性
bash scripts/golden-run/golden-run.sh       # 黄金 run 冒烟(进程内 ASGI,零网络,9 阶段)
node scripts/check-audit-allowlist.cjs      # 安全豁免到期校验(allowlist 必须带 owner+expires)
npm run docs:check                          # = bash scripts/check-docs.sh
docker compose -f deploy/docker-compose.yml config -q   # 部署编排静态校验
npx nx release --dry-run                    # 发布流程预演(不落改动;实际发版由维护者执行)
```

覆盖率阈值:Python 11 个项目在各自 `pyproject.toml` 的 addopts 里带 `--cov-fail-under`(common 84 / database 88 / events 96 / agent-orchestrator 82 / llm-gateway 95 / connector 91 / knowledge 90 / account 77 / workspace 81 / tool-service 82 / bff 81);TS 5 个包的 v8 thresholds 单源在根 `vitest.config.mts` 的 `export const thresholds`。改门禁数字时两处一起改。

CI(`.github/workflows/ci.yml`)共 9 job:setup / docs-check / security-audit / affected-js / python-lint×11 / python-test×11 / lock-check / alembic-check×6(真 Postgres)/ docker-build。改完 workflow 必须 `js-yaml` 解析 + 校验 `${{` 与 `}}` 成对。

### Python(common / database / events / agent-orchestrator / llm-gateway / connector-service / knowledge-service / account-service / workspace-service / tool-service / bff)

```sh
npx nx run common:sync  # uv sync:同步仓库根共享 .venv(依赖变更后必跑)
npx nx lint common      # ruff check
npx nx format common     # ruff format
npx nx test common       # uv run pytest(含覆盖率,报告写入根 reports/ 与 coverage/)
npx nx build common      # hatchling 构建 wheel/sdist
npx nx run agent-orchestrator:serve  # uvicorn :8002(run 编排 API)
npx nx run llm-gateway:serve         # uvicorn :8003(模型网关)
npx nx run connector-service:serve   # uvicorn :8004(连接器服务)
npx nx run knowledge-service:serve   # uvicorn :8006(知识库)
npx nx run account-service:serve     # uvicorn :8001(账号/组织)
npx nx run workspace-service:serve   # uvicorn :8005(工作区/交付物)
npx nx run tool-service:serve        # uvicorn :8008(内置工具服务:工具目录/执行/审计)
npx nx run bff:serve                 # uvicorn :8007(FastAPI BFF,同源 /api)
```

`common`/`database`/`events` 是共享库,含 lint/format/test/build/sync/add/remove;`agent-orchestrator`/`llm-gateway`/`connector-service`/`knowledge-service`/`account-service`/`workspace-service`/`tool-service`/`bff` 是应用(无 build target):`agent-orchestrator` 测试走 aiosqlite 文件库,不依赖外部 DB;`llm-gateway` 测试对 litellm 全打桩,不发真实请求;`connector-service` 测试只覆盖连接列表/撤销与鉴权,零网络;`knowledge-service` 默认用本地词法嵌入提供方,网关提供方用 httpx.MockTransport 打桩,零网络;`account-service`/`workspace-service` 测试走 aiosqlite 文件库 + 临时对象目录;`tool-service` 测试对 workspace-service 沙箱与知识服务全打桩(FakeSandboxClient/FakeToolClient),零网络。

**注意**:`npx nx sync <项目>` 会被 Nx 内置的 sync-generators 命令抢占而不执行 uv sync,必须用 `npx nx run <项目>:sync` 显式形式。

### 依赖管理

**Python 一律走 nx target(底层是 uv),不要直接 `pip install`:**

```sh
npx nx run common:add requests                     # 新增依赖(写项目 pyproject.toml,更新根 uv.lock)
npx nx run common:add --name pytest --group dev     # dev 依赖(写入该项目 [dependency-groups].dev)
npx nx run common:remove requests
npx nx run common:lock --update                     # 更新 uv.lock
npx nx run common:sync                              # 变更后同步 .venv
```

**JS** 修改对应项目 `package.json` 后在仓库根执行 `npm install`(npm workspaces,根 `package-lock.json` 锁定)。受限环境(如 agent 沙箱)下 `~/.npm` 不可写会直接失败,用仓库本地缓存:`npm_config_cache="$PWD/.npm-cache" npm install ...`(该目录已被 gitignore)。

## 代码风格

- **TypeScript**:`strict: true`、ESM、`nodenext` 模块解析、target es2022(根 `tsconfig.base.json`);Prettier 单引号(根 `.prettierrc`);ESLint 9 flat config 统一于 `tools/eslint-config`,格式交给 Prettier、ESLint 只管质量。
- **包命名**:JS 包统一 `@beauty/<目录名>` 作用域(根包 `@beauty/source`);应用目录名不带 `beauty-` 前缀;Python 共享库实名命名(`common`/`database`/`events`/`agent-kit`…),不用 `py-` 前缀,并避开 stdlib 与主流库的顶层名(如 `agents`、`platform`)。
- **`apps/web`**:路径别名 `@` → `src/`(`vite.config.ts`);基础组件统一从 `@beauty/design` 引用(本地不再维护 shadcn/ui 副本),主题令牌经 `@beauty/design/tailwind.css` 引入。Vite dev 把 `/api`→FastAPI BFF(`:8007`,BFF 再转发 runtime/account/workspace/knowledge),`/knowledge-api`→knowledge-service(`:8006`)、`/account-api`→account-service(`:8001`)、`/workspace-api`→workspace-service(`:8005`) 保留给 token 模式联调,所以本地先起 BFF 与对应服务;界面是 ardot 设计稿《Harness Agent Web 控制台》落地的**暗色控制台**(`src/index.css` 的 `--ha-*` 令牌 + 靛蓝主色);**结构分工**(W6 收敛,见 docs/refactoring/verification/007-w6.md):`src/components/console/` 只留外壳(console-layout/sidebar/topbar/status-bar)与通用原语(ui/dialog/button-class/markdown/diff-view),业务域组件一律在 `src/features/<domain>/`(16 个域,数据层是 `features/<domain>/use-<domain>-model.ts`),`pages/` 只做组装、不写取数;体量与分层由 `apps/web/eslint.config.mjs` 兜底:源文件 ≤400 行(测试 ≤500,warn)、`features/**` 不得 import `@/pages`、console 原语不得 import `@/features`;页面:`/` 是**工作台/会话工作台**(`src/pages/chat.tsx` + `src/lib/conversations-api.ts`:发消息走 SSE 流式(`/messages/stream` 的 meta/delta/done)——**先由服务端分流**:简单对话直接由模型答一条(**正文边产出边渲染**,不建 run、不需审批),复杂任务才建 run 并规划(进行中则并入追加指令);发送方式可选 自动/对话/任务;Agent 回复由服务端落成 assistant 消息,实时过程由事件流翻译;多轮上下文由服务端注入;审批/确认就地给按钮)、`/runs`(任务编排看板 + 运行详情抽屉,`src/lib/runs-api.ts`)、`/changes`(变更与产物/Diff 查看器,`src/lib/workspace-api.ts`)、`/knowledge`(知识库入库/清单/检索预览,`src/lib/knowledge-api.ts`)、`/settings`(组织/成员/会话,`src/lib/account-api.ts`)、`/workspace`(工作区中心:工作区清单/新建/重命名/删除工作区/选中工作区的会话与交付物,`src/lib/workspace-api.ts`;侧栏不再平铺工作区,改用**一行切换器 + 下拉菜单**(`src/features/workspace/workspace-switcher.tsx`:触发器显示当前工作区,菜单里切换/带会话数/「添加工作区…」「管理工作区…」,底部沙盒边界提示;「管理工作区」弹窗就地重命名/删除,默认工作区不可删),会话区只列**当前工作区**的会话(标题「<工作区> 的会话」),归属为空或指向已删工作区的会话收进「未分组」一段,搜索时改为扁平结果,URL `?workspace=<id>` 是当前工作区唯一真源——失效 id 自动回落默认/第一个;默认工作区由系统维护「可改名不可删」,删除工作区会级联清掉其交付物、会话保留并落「未分组」)、`/outbox`(事件队列运维:计数/过滤/人工重放,`src/lib/outbox-api.ts`,同源走 `/api`)、旧 `/landing` 深链(跳转到独立官网 `apps/site`)、`/login`(独立登录页,`src/pages/login.tsx`,不套站点布局:token 模式邮箱+密码登录/注册新组织/粘贴令牌联调,logto 模式跳 IdP);各页面共用 `src/lib/http.ts`(统一错误契约 + 请求封装,access token 过期时用 refresh 自动续期并重试一次)。产品页已下线(`/products` 走 404)。鉴权统一走 `src/lib/auth.ts`(`VITE_AUTH_MODE=token|session|logto`,默认 `token`,配置见 `apps/web/.env.example`):`token` 是自建认证——account-service 邮箱密码登录,双令牌存 localStorage,刷新走 `/v1/auth/refresh` 旋转式单飞;`session` 是生产/推荐模式——登录走 BFF `/api/auth/*`,双令牌只放加密 httpOnly Cookie,请求带 CSRF header;`logto` 走 SDK 登录;租户标识只在 token claim 里,前端不传租户头;守卫 `src/components/auth-guard.tsx` 拦下未登录访问送回 `/login` 并记住来路(登录/回调后跳回)。
- **品牌标识在 `apps/web/src/components/brand/logo.tsx`**:官方几何**内联**成组件(不引外部图片 → 只有一份来源);
  按尺寸分工:`BrandChip` 用官方 `favicon.svg` 几何(小尺寸下云镂空占比更大,24~32px 才读得出)+ 夜空底;
  `BrandMark` 用官方 `logo-mark.svg` 纯图形供大尺寸装饰位;控制台/官网用
  `BrandTile`(靛蓝方块 + 白色月云)与 `BrandLockup`(标识牌 + 无衬线产品名)。字标 `BrandWordmark` 是衬线小写,
  **把 `moon` 的两个 o 做成月相**(满月 + 月牙)——设计动作只花在这一处,其余字母单一墨色:
  试过的"某词加粗+渐变"是最容易读成机器生成的套路,已弃;月牙也用遮罩挖圆做厚,细牙小尺寸会看成细环。
  品牌变量 `--logo-moon` 定义在 `apps/web/src/index.css`(亮色取品牌暖玫瑰,暗色取官方月光米),
  字标月相与标识共用该色,形成呼应。
- **`packages/design`**:组件样式是普通 CSS + `bd-` 前缀 BEM 语义类名(如 `.bd-button--primary`),颜色/圆角/字体只读 `--bd-*` 令牌;组件源码里**不使用** Tailwind 工具类。
- **Python**:ruff 统一 lint + format;`common`、`database`、`events`、`agent-orchestrator`、`llm-gateway`、`connector-service`、`knowledge-service`、`account-service`、`workspace-service`、`tool-service`、`bff` line-length 88(各自 `pyproject.toml`)。

## 关键机制(改代码前必读)

- **`@beauty/source` 源码直连条件**:包 `exports` 中的 `@beauty/source` 条件指向 `src/`,由 `tsconfig.base.json` 的 `customConditions` 与 `apps/web/vite.config.ts` 的 `resolve.conditions` 启用——workspace 内包间引用直接消费源码,改组件即时生效、无需先构建;仓库外/发布走 `dist/`。两条路径共用同一份源码。
- **共享 venv**:uv workspace 全仓库只有一份 `.venv`(仓库根)和一份 `uv.lock`(提交 git)。Python 依赖任何变更后必须 `npx nx run <项目>:sync`。uv 默认缓存(`~/.cache/uv`)在受限环境(如 agent 沙箱)不可写时,改用仓库本地缓存:`export UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python"`。**注意**:`uv run` 按当前项目的 dev group 精确同步共享 venv,其他项目 dev group 的包会被剪掉——测试需要的依赖(`pytest-asyncio`、`aiosqlite` 等)必须写进**本项目**的 `[dependency-groups].dev`,不能指望兄弟项目替你装。
- **uv workspace members 是显式清单**:根 `pyproject.toml` 的 `members` 不能用 glob(`apps/`、`packages/` 下混有 npm 项目,glob 会命中无 `pyproject.toml` 的目录导致 uv 报错);新 Python 项目需登记 members + dependencies + `[tool.uv.sources]` 后 `npx nx run <项目>:sync`。
- **数据访问走仓储**:业务查询经 `database.repository`(`TenantRepository` 自动带 tenant_id、
  跨租户一律 404 不泄漏存在性、404 的 details 键名由 `id_detail_key` 声明以保持各服务既有契约);
  **仓储不提交**——事务边界只由 `session_scope`(HTTP 一请求一事务)或 `UnitOfWork`(脚本/worker/测试)负责。
  **全部服务已收敛**;复杂查询以显式命名方法留在各自仓储(如知识库 `searchable_rows`、
  orchestrator `next_seq`/`list_for_run`),不藏进通用抽象。
- **跨服务事件走 outbox**:`events.outbox` 保证"状态改了事件一定发、事务回滚事件一定不发"——
  投递是**至少一次**,消费方必须用 `events.outbox.mark_processed` 按事件 id 去重;
  失败按 `backoff_seconds` 退避,超 `max_attempts` 落 `failed` 留痕(不静默丢)。
  orchestrator 侧开关:`OUTBOX_ENABLED`(记录+dispatcher)与 `KNOWLEDGE_INGEST_ENABLED`(交付物自动入库);
  运维面 `/api/v1/outbox/*`(列表/计数/单条重放/批量重放,租户隔离;未启用时 409 `outbox.disabled`)。
- **Postgres 集成测试需要 Docker 或现成实例**:`packages/database/tests/test_postgres_integration.py`
  优先读 `TEST_POSTGRES_URL`,否则用 testcontainers 起 `postgres:16-alpine`;两者都不可用时**整体 skip 并打印原因**
  (沙箱里即如此)。
- **测试编排**:`nx.json` 中 `test` 目标 `dependsOn: ["^build"]`,跑测试前会自动构建上游依赖。
  **测试必须自足、零网络**,两道防线缺一不可:`Settings(_env_file=None)` 不读本地 `.env`,
  并**显式钉住所有会连外部的开关**(`llm_enabled`/`tools_enabled`/`outbox_enabled` 等)——
  **Nx 会把项目 `.env` 注入任务环境,环境变量优先级高于 `_env_file=None`**;
  只做前者时 `npx nx test` 会真的调用模型网关(直跑 `uv run pytest` 却正常,极难察觉:
  表现为测试变慢十倍、桩路径断言莫名失败、还烧真实 token)。
- **vitest workspace 模式**:根 `vitest.config.mts` 收集各项目的 vitest/vite 配置;`apps/*` 以各自的 `vitest.config.mts` 为准。

## 禁止事项

- 不要手动编辑生成物与缓存:`node_modules/`、`dist/`、`out-tsc/`、`doc_build/`、`.venv/`、`.uv-cache/`、`.uv-python/`、`.cache/`、`.nx/`、`reports/`、`coverage/`、`.playwright-mcp/`。
- `uv.lock`、`package-lock.json` 只能通过对应包管理命令更新,不要手改。
- 各服务目录下的 `.env` 被 git 忽略且由 pydantic-settings 运行时读取,不要提交密钥或覆盖它。

## 修改后的验证清单

- **TS/React 代码**:`npx nx typecheck <项目>` + `npx nx test <项目> -- --run` + `npx nx lint <项目>`;改动包公共 API 时再 `npx nx build <项目>`。
- **Python 代码**:`npx nx lint <项目>` + `npx nx format <项目>`;`common`/`database`/`events`/`agent-orchestrator`/`llm-gateway`/`connector-service`/`knowledge-service`/`account-service`/`workspace-service`/`tool-service` 另跑 `npx nx test <项目>`。
- **模型/迁移(易踩坑)**:改 ORM 表结构必须配 alembic 迁移——服务默认 `AUTO_CREATE_TABLES=false`,
  而测试走 `create_all` 会自动建表,**缺迁移在测试里看不出来、真实库上直接 500**;
  本地 dev 启动前由 `scripts/migrate-local.sh` 自动升级并按需「收养」SQLite 库
  (见 `docs/local-dev.md` §7.1);改完在服务目录内跑 `uv run alembic check` 确认模型与迁移一致。
- **依赖变更**:Python 先 `nx run <项目>:add/remove` 再 `nx run <项目>:sync`;JS 改 `package.json` 后根目录 `npm install`。
