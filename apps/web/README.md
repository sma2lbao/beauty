# web

beauty moon 的 Web 端（包名 `@beauty/web`）：工作区内的会话页 + 工作区中心 + 运行控制台 + 知识库 / 事件队列 / 账号。React + TypeScript SPA，位于
monorepo 的 `apps/web` 目录下，由 npm workspaces 管理、Nx 识别。

## 技术栈

| 类别 | 选型                                                                  |
| ---- | --------------------------------------------------------------------- |
| 框架 | React 19 + TypeScript                                                 |
| 路由 | react-router v8（`BrowserRouter` + 嵌套路由）                         |
| 数据 | @tanstack/react-query（服务端状态）                                   |
| 状态 | zustand（客户端 UI/看板状态，侧栏偏好持久化）                         |
| 样式 | Tailwind CSS v4（`@tailwindcss/vite` 插件，CSS 变量主题）             |
| 组件 | `@beauty/design`（monorepo 内组件库，源码直连 `packages/design/src`） |
| 测试 | Vitest + Testing Library（jsdom 环境，`vitest.config.mts`）           |
| 构建 | Vite 8                                                                |

## 常用命令

在仓库根目录运行（或 `cd apps/web` 后用 npm run）：

```sh
npx nx dev @beauty/web        # 启动 dev server（默认 5173 端口）
npx nx build @beauty/web      # tsc 类型检查 + vite 生产构建
npx nx preview @beauty/web    # 预览生产构建
npx nx typecheck @beauty/web  # 仅类型检查
npx nx test @beauty/web       # vitest + Testing Library 单测
```

## 目录结构

```
apps/web/
├── index.html
├── vite.config.ts              # react + tailwindcss 插件，@ -> src 别名
├── vitest.config.mts           # vitest 配置（jsdom 环境，复用 @ 别名）
└── src/
    ├── main.tsx                # 入口：QueryClientProvider + BrowserRouter
    ├── app.tsx                 # 路由表（SiteLayout 嵌套布局）
    ├── stores/                 # zustand：UI 偏好、任务看板选中/筛选
    ├── index.css               # Tailwind v4 主题：色板 / 字体 / 满月光盘样式
    ├── vitest.setup.ts         # 测试 setup：jest-dom 匹配器 + cleanup
    ├── components/
    │   ├── layout/             # 页头、页脚、站点布局
    │   ├── runs/               # run 相关业务组件
    │   └── brand/              # 品牌标识（官方几何内联）
    ├── lib/
    │   ├── products.ts         # mock 产品接口（后续替换为真实 API）
    │   └── utils.ts            # cn()（clsx + tailwind-merge）
    └── pages/
        ├── home.tsx            # / 首页：品牌 hero + 系列 teaser
        ├── products.tsx        # /products 产品列表（useQuery + 心愿单交互）
        ├── about.tsx           # /about 品牌故事
        └── not-found.tsx       # 404

测试文件与源码同目录（*.test.ts / *.test.tsx），覆盖 lib、ui 组件、
布局、路由与各页面。
```

## 工作区与会话（交互契约）

交互参照 dsh（DeepSeek Harness）的「工作区 / 会话」模型：**会话必须归属于某个工作区**（服务端也强制：不传工作区时落租户默认工作区）。

- `?workspace=<id>` 是当前工作区的**唯一真源**（`src/lib/workspace-selection.ts` 的 `useWorkspaceSelection`）：URL 没带时用默认工作区补上并 `replace` 回 URL；切换工作区会清掉当前会话/新建标记。
- `/`：会话页。`/?workspace=<id>&conversation=<id>` 打开某段会话，`/?workspace=<id>&new=1` 直接进新会话输入态；新会话空态里可以「选择工作区」，文案提示「会话将保存在「X」中」。
- `/workspace`：工作区中心。左侧工作区清单（每行有 `⋯` 菜单可重命名 / 删除）内联新建表单，右侧是选中工作区的会话、交付物与产物预览；`/workspace?new=1` 会展开并聚焦新建工作区表单。
- 侧栏不再平铺工作区，改用**一行切换器 + 下拉菜单**（`src/components/console/workspace-switcher.tsx`）：触发器显示当前工作区，点开菜单即可切换（带会话数与当前项对勾）、「添加工作区…」「管理工作区…」，底部提示沙盒边界；「管理工作区」弹窗内就地重命名 / 删除（默认工作区不可删）。会话区只列**当前工作区**的会话（标题「<工作区> 的会话」），归属为空或指向已删工作区的会话收进「未分组」一段；搜索时改为扁平结果。
- 工作区生命周期：默认工作区由系统维护（`is_default`，可改名、不可删除）；删除工作区会级联清掉该工作区的交付物，但会话不跨服务级联删除——它们保留并落「未分组」，URL 上失效的 `?workspace=` 由 `useWorkspaceSelection` 自动回落到默认 / 第一个工作区。

## 约定

- **路径别名**：应用内用 `@/` 指向 `src/`（vite.config.ts 与 tsconfig.json 保持同步）。
- **组件来源**：基础组件（Button / Card / Input / Textarea / Select / Tag / Spin / Empty 等）
  统一从 `@beauty/design` 引用，本地不再维护 shadcn/ui 副本；需要新组件时先在
  `packages/design` 补齐（含单测与文档），再到 web 消费。
- **数据请求**：页面统一通过 `useQuery` + queryKey 取数，`src/lib/products.ts`
  是唯一的 mock 层，接后端时只改这一个文件。
- **主题**：色板与设计令牌由 `@beauty/design/tailwind.css` 提供（Vercel 风格单色灰阶，
  亮暗双主题），应用只在自己的 `index.css` 里补字体与品牌月相色；页头按钮切换深浅色，偏好写入 localStorage。
- **测试**：页面测试通过 `vi.mock('@/lib/products')` 控制 mock 接口的
  返回（成功 / 失败 / 挂起），不依赖真实延时；组件测试只断言行为与可访问性。
