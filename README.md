# beauty-moon

基于 [Nx](https://nx.dev) 管理的 monorepo，包含 **TypeScript** 工具库（`packages/*`）、**React 前端应用**（`apps/*`）与 **Python** 库三类项目；Python 侧统一使用 [uv](https://docs.astral.sh/uv/) 管理依赖与虚拟环境（通过 [nxlv/python](https://github.com/lucasvieirasilva/nx-plugins) 插件集成到 Nx）。

## 环境要求

| 工具    | 版本                  | 说明                                                    |
| ------- | --------------------- | ------------------------------------------------------- |
| Node.js | >= 20.19（本机 24.x） | 运行 Nx 与 JS 工具链                                    |
| npm     | 随 Node               | 安装 JS 依赖                                            |
| Python  | 3.12.x                | uv workspace 共享 venv，由根 `.python-version` 统一锁定 |
| uv      | >= 0.5                | Python 包管理 / 虚拟环境 / 构建                         |

## 目录结构

```
beauty-moon/
├── apps/
│   ├── web/                    # 品牌前台站点（React + react-router + react-query + Tailwind + shadcn/ui）
│   │   ├── src/                # 页面 / 组件 / 路由
│   │   ├── index.html
│   │   └── vite.config.ts      # react + tailwindcss 插件，@ -> src 别名
│   └── ink/                    # 本地优先个人知识库（Python + LlamaIndex + ChromaDB RAG 问答）
├── packages/
│   ├── ts-utils/               # TypeScript 库示例（tsc 构建 + vitest 测试）
│   │   ├── src/                # 源码
│   │   ├── vitest.config.mts   # 测试配置
│   │   └── package.json
│   └── py-utils/               # Python 库示例（uv + pytest + ruff + hatchling）
│       ├── py_utils/           # Python 包源码
│       ├── tests/              # pytest 测试
│       ├── pyproject.toml      # 依赖 / 构建 / ruff / pytest 配置
│       └── project.json        # Nx 目标定义
├── tools/
│   └── eslint-config/          # 共享 ESLint flat config(@beauty/eslint-config)
│       ├── configs/            # base.js / react.js 配置工厂
│       ├── index.js            # 导出 base / react
│       └── package.json         # eslint 插件依赖全部收敛于此
├── pyproject.toml              # uv workspace 容器（共享 venv）
├── .python-version             # 锁定 Python 3.12（整个 workspace）
├── uv.lock                     # uv 锁文件（所有 Python 项目共享，提交到 git）
├── nx.json                     # Nx 配置
├── tsconfig.base.json          # TS 基础配置（customConditions: @beauty/source）
└── package.json                # JS 依赖 + npm workspaces(packages/*、apps/* 与 tools/*)
```

## 常用命令

```sh
npx nx graph                     # 打开项目依赖关系图
npx nx run-many -t build         # 构建所有可构建项目
npx nx run-many -t test          # 运行所有测试
npx nx run-many -t lint          # 全仓库 lint(JS/TS 用 ESLint,Python 用 ruff)
npx nx show projects             # 列出所有项目
```

### TypeScript（ts-utils）

```sh
npx nx build @beauty/ts-utils   # tsc 构建到 dist/
npx nx test @beauty/ts-utils    # vitest 单测
npx nx typecheck @beauty/ts-utils
```

### 前端（web）

```sh
npx nx dev @beauty/web        # 启动 dev server（默认 5173 端口）
npx nx build @beauty/web      # tsc 类型检查 + vite 生产构建
npx nx preview @beauty/web    # 预览生产构建
npx nx typecheck @beauty/web  # 仅类型检查
npx nx lint @beauty/web        # ESLint(共享配置 tools/eslint-config)
npx nx test @beauty/web       # vitest + Testing Library 单测
```

React 19 + TypeScript SPA：react-router v8 路由、@tanstack/react-query 数据层、Tailwind CSS v4 + shadcn/ui 组件，详见 [apps/web/README.md](apps/web/README.md)。

### 知识库（ink）

```sh
npx nx sync ink           # uv sync：更新仓库根共享 .venv
npx nx lint ink           # ruff 检查
npx nx format ink         # ruff 格式化
npx nx serve ink          # Streamlit 聊天 UI（8501 端口）
```

本地优先 RAG 问答应用（LlamaIndex + DeepSeek + bge-m3 + ChromaDB），详见 [apps/ink/README.md](apps/ink/README.md)。

### Python（py-utils）

所有 Python 项目共享仓库根的 `.venv`（uv workspace，根 `pyproject.toml` 统一管理，Python 3.12）。

```sh
npx nx sync py-utils     # uv sync：更新仓库根共享 .venv
npx nx lint py-utils     # ruff 检查
npx nx format py-utils   # ruff 格式化
npx nx test py-utils     # pytest（含覆盖率与 HTML 报告）
npx nx build py-utils    # hatchling 构建 wheel/sdist 到 dist/
```

测试报告输出在根目录 `reports/` 与 `coverage/`（已被 git 忽略）。

## 新增项目

### TypeScript

```sh
npx nx g @nx/js:lib packages/<名称> --unitTestRunner=vitest --bundler=tsc
```

生成器沿用根 package.json 的 npm 作用域，新包命名为 `@beauty/<名称>`。

新项目接入共享 ESLint 配置(`tools/eslint-config`)只需三步:

1. 项目 `package.json` 的 `devDependencies` 加上 `"@beauty/eslint-config": "*"`(或 `npm install -D @beauty/eslint-config -w <项目名>`);
2. 项目根创建 `eslint.config.mjs`:React 项目用 `react()`,纯 TS 包用 `base()`;
3. 运行 `npx nx lint <项目名>` 验证(`nx.json` 已注册 `@nx/eslint` 插件,含该文件的项目自动获得 lint target)。

```js
// eslint.config.mjs —— React 项目
import { react } from '@beauty/eslint-config';

export default react();
```

```js
// eslint.config.mjs —— 纯 TS 包
import { base } from '@beauty/eslint-config';

export default base();
```

### Python（uv）

```sh
npx nx g @nxlv/python:uv-project <项目名> \
  --projectType=library \
  --directory=packages/<目录名> \
  --moduleName=<python模块名> \
  --pyenvPythonVersion=3.12 \
  --no-interactive
```

`--projectType=application` 可创建应用类项目（附带 `serve` 目标）。

## Python 依赖管理（uv）

```sh
npx nx run py-utils:add requests      # 新增依赖（uv add）
npx nx run py-utils:add --name pytest --group dev    # 新增 dev 依赖
npx nx run py-utils:remove requests   # 移除依赖
npx nx run py-utils:lock --update     # 更新 uv.lock
npx nx run py-utils:sync              # 同步 .venv
```

依赖统一写在各项目的 `pyproject.toml`（dev 依赖合并到根 `[dependency-groups].dev`），锁定信息提交根 `uv.lock`（全 workspace 共享一份）。

## 其他

- **包作用域**：JS 包统一使用 `@beauty/*` 作用域（根包为 `@beauty/source`）；应用目录名不带 `beauty-` 前缀（`apps/web`、`apps/ink`），JS 包名即 `@beauty/<目录名>`。
- **源码解析**：`tsconfig.base.json` 通过 customConditions 让包间解析直接指向源码。
- **共享 venv**：所有 Python 项目共用仓库根 `.venv`（uv workspace），Python 统一 3.12，依赖只装一份。
- **发布**：Python 项目已内置 `@nxlv/python` 的 release 集成，TS 包可用 `npx nx release`。
