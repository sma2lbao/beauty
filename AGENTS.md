# AGENTS.md

**beauty-moon** 是基于 [Nx](https://nx.dev) 的 polyglot monorepo:JS/TS 走 npm workspaces(`packages/*`、`apps/*`),Python 走 [uv](https://docs.astral.sh/uv/) workspace(经 [@nxlv/python](https://github.com/lucasvieirasilva/nx-plugins) 插件编排)。所有命令默认在仓库根目录执行。

## 项目结构

| 路径                  | Nx 项目名               | 类型         | 技术栈                                                                                  |
| --------------------- | ----------------------- | ------------ | --------------------------------------------------------------------------------------- |
| `apps/web`            | `@beauty/web`           | React 应用   | React 19 + react-router v8 + @tanstack/react-query + Tailwind CSS v4 + shadcn/ui,Vite 8 |
| `apps/ink`            | `ink`                   | Python 应用  | LlamaIndex + DeepSeek + bge-m3 + ChromaDB 的 RAG 问答,Streamlit UI                      |
| `packages/ts-utils`   | `@beauty/ts-utils`      | TS 库        | tsc 构建 + vitest                                                                       |
| `packages/design`     | `@beauty/design`        | React 组件库 | 纯 CSS + `bd-` 前缀 BEM 类名 + `--bd-*` 设计令牌;rspress 文档站                         |
| `packages/py-utils`   | `py-utils`              | Python 库    | hatchling + pytest + ruff                                                               |
| `tools/eslint-config` | `@beauty/eslint-config` | 共享工具包   | ESLint 9 flat config(`base`/`react` 工厂);eslint 插件依赖收敛于此                       |

环境要求:Node.js >= 20.19;Python 3.12(根 `.python-version` 锁定);uv >= 0.5。

## 常用命令

### 全仓库

```sh
npx nx show projects        # 列出所有项目
npx nx run-many -t lint     # 全仓库 lint(JS/TS 用 ESLint,Python 用 ruff)
npx nx run-many -t build    # 构建所有可构建项目
npx nx graph                # 打开项目依赖关系图
```

### TypeScript / React

`nx.json` 配置了 `testMode: "watch"`,因此 **`npx nx test <项目>` 是 vitest watch 模式,会挂住不退出**。脚本/agent 场景务必追加 `-- --run`:

```sh
npx nx test @beauty/web -- --run       # 一次性运行测试(推荐)
npx nx test @beauty/design -- --run
npx nx test @beauty/ts-utils -- --run

npx nx dev @beauty/web                 # Vite dev server(5173 端口,交互开发用)
npx nx build @beauty/web               # tsc --noEmit + vite 生产构建
npx nx typecheck @beauty/web           # 仅类型检查(web/design/ts-utils 同名)
npx nx lint @beauty/web                # ESLint(共享配置 tools/eslint-config)
npx nx lint @beauty/web -- --fix       # ESLint 自动修复
npx nx build @beauty/design            # tsc emit ESM 到 dist
npx nx run @beauty/design:docs:dev     # rspress 文档站
```

注意:`test-ci` target 是 atomized task,本地运行要求 Nx Cloud,请勿使用。

### Python(ink / py-utils)

```sh
npx nx sync py-utils      # uv sync:同步仓库根共享 .venv(依赖变更后必跑)
npx nx lint py-utils      # ruff check
npx nx format py-utils     # ruff format
npx nx test py-utils       # uv run pytest(含覆盖率,报告写入根 reports/ 与 coverage/)
npx nx build py-utils      # hatchling 构建 wheel/sdist
npx nx serve ink           # Streamlit UI(8501 端口)
```

`ink` 未定义 test target;可 lint/format/sync/serve。

### 依赖管理

**Python 一律走 nx target(底层是 uv),不要直接 `pip install`:**

```sh
npx nx run py-utils:add requests                     # 新增依赖(写项目 pyproject.toml,更新根 uv.lock)
npx nx run py-utils:add --name pytest --group dev     # dev 依赖(合并到根 [dependency-groups].dev)
npx nx run py-utils:remove requests
npx nx run py-utils:lock --update                     # 更新 uv.lock
npx nx sync py-utils                                  # 变更后同步 .venv
```

**JS** 修改对应项目 `package.json` 后在仓库根执行 `npm install`(npm workspaces,根 `package-lock.json` 锁定)。

## 代码风格

- **TypeScript**:`strict: true`、ESM、`nodenext` 模块解析、target es2022(根 `tsconfig.base.json`);Prettier 单引号(根 `.prettierrc`);ESLint 9 flat config 统一于 `tools/eslint-config`,格式交给 Prettier、ESLint 只管质量。
- **包命名**:JS 包统一 `@beauty/<目录名>` 作用域(根包 `@beauty/source`);应用目录名不带 `beauty-` 前缀。
- **`apps/web`**:路径别名 `@` → `src/`(`vite.config.ts`);shadcn/ui 组件源码直接放在 `src/components/ui`。
- **`packages/design`**:组件样式是普通 CSS + `bd-` 前缀 BEM 语义类名(如 `.bd-button--primary`),颜色/圆角/字体只读 `--bd-*` 令牌;组件源码里**不使用** Tailwind 工具类。
- **Python**:ruff 统一 lint + format;`py-utils` line-length 88,`ink` line-length 100(各自 `pyproject.toml`)。

## 关键机制(改代码前必读)

- **`@beauty/source` 源码直连条件**:包 `exports` 中的 `@beauty/source` 条件指向 `src/`,由 `tsconfig.base.json` 的 `customConditions` 与 `apps/web/vite.config.ts` 的 `resolve.conditions` 启用——workspace 内包间引用直接消费源码,改组件即时生效、无需先构建;仓库外/发布走 `dist/`。两条路径共用同一份源码。
- **共享 venv**:uv workspace 全仓库只有一份 `.venv`(仓库根)和一份 `uv.lock`(提交 git)。Python 依赖任何变更后必须 `npx nx sync <项目>`。
- **测试编排**:`nx.json` 中 `test` 目标 `dependsOn: ["^build"]`,跑测试前会自动构建上游依赖。
- **vitest workspace 模式**:根 `vitest.config.mts` 收集各项目的 vitest/vite 配置;`apps/*` 以各自的 `vitest.config.mts` 为准。

## 禁止事项

- 不要手动编辑生成物与缓存:`node_modules/`、`dist/`、`out-tsc/`、`doc_build/`、`.venv/`、`.uv-cache/`、`.uv-python/`、`.cache/`、`.nx/`、`reports/`、`coverage/`、`.playwright-mcp/`。
- `uv.lock`、`package-lock.json` 只能通过对应包管理命令更新,不要手改。
- `.env` 被 git 忽略且由 ink 运行时读取(pydantic-settings),不要提交密钥或覆盖它。

## 修改后的验证清单

- **TS/React 代码**:`npx nx typecheck <项目>` + `npx nx test <项目> -- --run` + `npx nx lint <项目>`;改动包公共 API 时再 `npx nx build <项目>`。
- **Python 代码**:`npx nx lint <项目>` + `npx nx format <项目>`;`py-utils` 另跑 `npx nx test py-utils`。
- **依赖变更**:Python 先 `nx run <项目>:add/remove` 再 `nx sync`;JS 改 `package.json` 后根目录 `npm install`。
