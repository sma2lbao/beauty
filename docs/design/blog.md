# 学习博客与案例实验室设计

> 状态：已定稿（2026-11-18）；P0–P2 已落地（`apps/blog` 骨架与页面、`labs/` 围栏与 `check-labs.sh`），P3 待排期 · 范围：独立博客应用、一次性案例项目隔离；评论后置
> 关联：[architecture.md](../architecture.md)、[hybrid-deployment.md](./hybrid-deployment.md)、[local-dev.md](../local-dev.md)、[observability.md](../observability.md)、[tool-service.md](./tool-service.md)（服务骨架参照）
> 定位：本文是「博客 + 案例实验室」的单一设计稿。评论只保留未来契约，不在本期实施；架构层面的服务边界仍以 [architecture.md](../architecture.md) 为准。

## 1. 决策记录

2026-11-18 已拍板：

| #   | 决策                                                 | 落地影响                                                                |
| --- | ---------------------------------------------------- | ----------------------------------------------------------------------- |
| D1  | 博客做成**独立应用 `apps/blog`**，不塞进 `apps/site` | 新建 Nx 应用 + 独立 Vercel 项目/子域；内容、构建、发版与官网解耦        |
| D2  | 案例项目放**主仓 `labs/`**，用围栏隔离               | 需要 `.nxignore`、gitignore、ESLint/Prettier 忽略、workspace/锁文件断言 |
| D3  | **本期不做评论**                                     | 不新建 comment-service、不改网关、不动 account-service                  |
| D4  | 未来评论**必须登录**，不做匿名                       | 未来契约按「登录写、公开读」设计；当前没有评论代码                      |
| D5  | 文章**先全部公开**                                   | frontmatter 不设 `visibility`；草稿只在本地 dev 可见                    |
| D6  | **暂不改 account-service**                           | 不新增 scope、不改 introspect；未来做评论时再单独评估                   |

由 D1–D6 衍生的约束：

- 博客域名用 `blog.beautymoon.cn`（子域），官网 `site.beautymoon.cn` 导航加「博客」入口；不在官网做 `/blog` 反代。
- `apps/blog` 是纯静态站（`output: 'export'`），无服务端运行时、无数据库、无 `/api` 依赖。
- `labs/` 默认**不被 git 跟踪**，是一次性本地草稿；案例截图复制到博客自己的 `public/` 后再发布。
- 评论保留设计摘要（§7），但标记「未排期」；启动前需要重新确认 D3/D6。

## 2. 目标与非目标

### 2.1 目标

- 用 MDX 记录学习笔记：长文、TIL 短笔记、带可运行案例的实验记录。
- 博客独立构建/部署，文章数量增长不影响官网发布；视觉与官网同源但不共享代码生命周期。
- 案例项目可在本机快速创建、运行、丢弃，**不进入** npm workspaces、uv members、Nx 项目图、根锁文件、根 lint/格式化、CI affected 集合。
- 博客能引用案例的元数据与截图，但**构建不依赖**案例目录存在。
- 为未来评论留出清晰接口，但本期零评论代码、零账号改动。

### 2.2 非目标

- 评论、登录、审核、反垃圾（全部后置）。
- 在线 CMS/编辑器、多作者、团队协作、付费订阅、newsletter。
- 评论图片/视频、私信、实时推送、邮件通知。
- 博客 i18n（先中文）、全文搜索集群（> 30 篇再评估 Pagefind）。
- 把 `labs/` 变成正式 workspace 或共享依赖源。

## 3. 参考系统

### 3.1 内容平台

| 系统              | 形态                        | 可借鉴                           | 不适合本案的原因                           |
| ----------------- | --------------------------- | -------------------------------- | ------------------------------------------ |
| Docusaurus        | 文档 + 博客，MDX，版本化    | frontmatter、tags、RSS、系列     | React 文档重；博客是附属能力，独立应用更轻 |
| Astro Starlight   | 文件路由 + MDX，静态        | content collections + zod、零 JS | 新框架；与现有 React/设计体系重复          |
| Nextra / Fumadocs | Next 文档/博客，MDX         | 与 Next 生态贴合                 | 定制层与本案重叠，等于再包一层             |
| Hugo / Jekyll     | 静态生成，Markdown          | 构建快、部署简单                 | 组件化/交互弱，TS/Python 案例展示不便      |
| Ghost / WordPress | 自托管 CMS + 会员/插件      | 写作/发布体验                    | 重、运维成本高；会员体系与主仓账号冲突     |
| Hashnode / Dev.to | 托管社区                    | 分发、canonical                  | 数据/设计不可控，无法复用主仓账号          |
| Quartz            | Obsidian 数字花园           | `stage`、更新时间、双链          | 图谱可视化信息密度低；定制受限             |
| **Next 独立应用** | MDX + React 组件 + 静态导出 | 设计自由、案例组件、SEO          | 自己维护内容管线（本文工作量）             |

### 3.2 借鉴清单

- **借**：Docusaurus 的 frontmatter/tags/RSS/系列；Astro 的构建期 schema 校验；Quartz 的 `stage` 与「最后更新」；Dev.to 的 series/canonical；Simon Willison TIL 的短笔记形态。
- **不借**：数字花园的图谱可视化；CMS 在线编辑器；任何自带账号体系的评论后端。

### 3.3 为什么最终选独立应用

| 维度         | 官网内嵌 `/blog`       | **独立 `apps/blog`（选定）**      | 托管博客        |
| ------------ | ---------------------- | --------------------------------- | --------------- |
| 首次上线     | 1.5–2 人日             | 4–6 人日                          | 0.5 人日        |
| 与主仓隔离   | 中                     | 高                                | 无              |
| 设计一致性   | 高                     | 中高（复用令牌/组件）             | 低              |
| 独立发版     | 否                     | 是                                | 是              |
| 长期内容规模 | < 100 篇舒适           | 无上限                            | 无上限          |
| 结论         | 最省事，但博客绑死官网 | **内容与官网解耦，值得多花 2 天** | 账号/数据不可控 |

独立应用的代价是「多一个应用、多一个部署、多一套导航」；换来的是博客可以独立迭代、独立扩容、未来独立接评论，不会因为一次官网改版而回滚文章。

## 4. 架构总览

```text
site.beautymoon.cn   apps/site   (Next 静态导出, Vercel)  ──导航链接──▶  blog.beautymoon.cn
                                                                        apps/blog (Next 静态导出, Vercel)
                                                                          ├─ content/*.mdx
                                                                          ├─ public/blog/<slug>/*  (截图/GIF)
                                                                          └─ scripts/blog-feed.mjs → out/feed.xml

主仓 beauty/
  apps/blog/            博客（workspace 成员，进 Nx 图/CI）
  labs/<slug>/          一次性案例（gitignored、.nxignore、不在 workspaces/uv members）
  docs/design/blog.md   本文
```

- 两个站点之间只用普通链接，不共享 Cookie、不共享 `/api`。
- 博客没有后端；将来评论上线时再加 `comment-service` 与站点 rewrite（§7），不影响当前静态架构。
- `labs/` 与 `apps/blog` 是**两个独立关注点**：labs 不参与构建，博客只消费截图与元数据。

## 5. `apps/blog` 设计

### 5.1 应用骨架

```text
apps/blog/
  app/
    layout.tsx
    page.tsx                 # 首页/文章列表
    posts/[slug]/page.tsx
    tags/[tag]/page.tsx
    series/[name]/page.tsx
    archive/page.tsx
    sitemap.ts
    robots.ts
    globals.css
  components/
    site-header.tsx
    site-footer.tsx
    post-card.tsx
    post-layout.tsx
    toc.tsx
    demo-card.tsx
    mdx-components.tsx
    copy-button.tsx
  content/*.mdx
  lib/blog/
    schema.ts
    posts.ts
    mdx.tsx
    toc.ts
    seo.ts
  public/blog/<slug>/*
  public/og.png
  scripts/blog-feed.mjs
  blog.config.ts
  next.config.ts
  project.json
  package.json
  tsconfig.json
  vitest.config.mts
```

- 包名 `@beauty/blog`（遵循 `@beauty/<目录名>`），`private: true`。
- Nx 项目沿用 `apps/site` 的 target 形态：`dev`（`next dev`，端口 3001，避开 site 的 3000）、`build`（`next build`，输出 `out`）、`typecheck`（`tsc --noEmit`）、`test`（`vitest run`）、`lint`。
- `next.config.ts`：`output: 'export'`、`images.unoptimized: true`、`trailingSlash: false`、`transpilePackages: ['@beauty/design']`。
- 样式：Tailwind CSS v4 + `@beauty/design` 令牌；复用官网的 `--ha-*` 主题变量与 `bd-` 组件，博客只新增文章排版类（`blog-prose`），不复制一套设计系统。
- 依赖（实施时按需精简）：`next`、`react`、`react-dom`、`@beauty/design`、`next-mdx-remote`、`gray-matter`、`zod`、`remark-gfm`、`rehype-pretty-code`、`shiki`、`rehype-slug`、`rehype-autolink-headings`；dev：`vitest`、`jsdom`、`@testing-library/react`。
- `apps/blog` 自动成为 npm workspace 成员（根 glob `apps/*`），**无需改根 workspaces**；但要在根 `vitest.config.mts` 的 `thresholds` 增加 `@beauty/blog` 键，并在 Nx 图中出现。

### 5.2 路由

| 路由             | 渲染     | 说明                                                    |
| ---------------- | -------- | ------------------------------------------------------- |
| `/`              | SSG      | 文章列表：置顶/最新、标签筛选、系列入口、案例区         |
| `/posts/[slug]`  | SSG      | 文章详情；`generateStaticParams` 全量预渲染             |
| `/tags/[tag]`    | SSG      | 按标签聚合                                              |
| `/series/[name]` | SSG      | 系列目录（按 `series.order`）                           |
| `/archive`       | SSG      | 按年月归档                                              |
| `/feed.xml`      | 构建产物 | 构建后脚本生成，规避静态导出下 route handler 的版本差异 |
| `/search`        | 延后     | > 30 篇再上 Pagefind；v1 用标签/归档                    |

- slug 一经发布**不可改**；确需改名时在 `apps/blog/lib/blog/redirects.ts` 维护映射，Vercel `redirects` 出 301（静态导出无服务端）。
- 站内相对链接在构建期校验目标存在；外链不校验（构建不依赖网络）。
- 官网 `apps/site` 的 `NAV` 增加外链 `{ href: BLOG_URL, label: '博客' }`，页脚同步；博客 header 反向链接回官网。两处都用绝对 URL，避免跨域相对路径错误。

### 5.3 内容模型

frontmatter（zod 单一真源，`lib/blog/schema.ts`）：

```yaml
---
title: 用 200 行写一个可中断的 Agent 循环
slug: tiny-agent-loop
date: 2026-11-18
updated: 2026-11-20
summary: 从状态机到中断恢复，记录一次最小实现的取舍。
type: article # article | til | lab
stage: growing # seedling | growing | evergreen（可选）
tags: [agent, typescript, 架构]
series:
  name: agent-basics
  order: 2
draft: false
cover: /blog/tiny-agent-loop/cover.png
demo:
  path: labs/tiny-agent-loop
  language: ts # ts | python | mixed
  runtime: node22 # node22 | python3.12
  run: pnpm install && pnpm dev
  port: 4101
  status: archived # archived | live | wip
  repo: https://github.com/<owner>/beauty-labs # 可选：代码另行推送时
  screenshots:
    - /blog/tiny-agent-loop/demo-1.png
---
```

规则：

- `slug` 必须等于文件名（去 `.mdx`），`^[a-z0-9]+(?:-[a-z0-9]+)*$`，全站唯一。
- `summary` 必填（10–280 字），用于列表/SEO/RSS，不靠截断正文。
- `type: lab` 必须有 `demo`；`type: til` 可省略 `series`，详情页不渲染目录。
- `draft: true` 只在 dev 可见；生产构建排除。**不设 `visibility`**（D5 全公开）。
- `tags` ≤ 8，`series.order` 非负整数；同系列重复 order 构建失败。
- `updated` 缺省等于 `date`；列表显示「更新于」。
- `demo.path` 指向主仓 `labs/<slug>`（默认不跟踪）；`demo.repo` 仅当代码另行推送时填写。

### 5.4 内容管线

```text
content/*.mdx
  → gray-matter 解析 frontmatter
  → zod 校验（失败：文件 + 字段 + 期望值）
  → next-mdx-remote/rsc 编译（remark-gfm / rehype-pretty-code / rehype-slug / rehype-autolink-headings）
  → React Server Component 渲染
  → next build 静态导出到 out/**
  → 后置脚本生成 out/feed.xml
```

- **不引 `rehype-raw`**：作者不写原始 HTML，减少 XSS 面。
- **代码高亮**：`rehype-pretty-code` + Shiki，构建期出 HTML，运行时零高亮 JS；单暗色主题，避免闪白。
- **组件白名单**：`Callout`、`Figure`、`DemoCard`、`Steps`、`FileTree`、`Kbd`、`Badge`；全部服务端组件，只有「复制代码」是客户端小组件。
- **目录**：编译时抽取 `h2/h3`，生成右侧 sticky TOC；移动端折叠。
- **阅读时长**：中文 300 字/分钟、英文 200 词/分钟，展示「约 N 分钟」。
- **RSS**：`scripts/blog-feed.mjs` 在 `next build` 后读取 MDX，写 `out/feed.xml`（标题/链接/摘要/日期），并做 XML 合法性校验。
- **sitemap**：`app/sitemap.ts` 用 `force-static`，从 `lib/blog/posts` 读取文章/标签/系列页，`lastModified` 用 `updated`。
- **JSON-LD**：详情页注入 `BlogPosting`（headline/datePublished/dateModified/author/image），作者来自 `blog.config.ts`。
- **OG**：优先 `cover`，缺省 `/og.png`；v1 不做动态 OG 图。

### 5.5 视觉与交互

- 列表：首篇 featured 大卡 + 纵向列表；显示日期、阅读时长、标签、`stage` 徽标、系列。
- 详情：标题/摘要/元信息/正文（`max-width: 70ch`）/标签/上下篇；右侧 TOC（≥ xl）。
- 案例卡：截图、语言/运行时/端口、运行命令（一键复制）、`status` 徽标；不嵌 iframe（案例在本机跑）。
- 代码块：暗色、文件名标题、复制按钮、横向滚动不换行。
- 可访问性：跳转正文、焦点环、对比度、`aria-current`。
- 空态：无文章/无标签/无系列各有文案。

### 5.6 配置与部署

`blog.config.ts`：

```ts
export const blogConfig = {
  title: 'beauty moon · 学习笔记',
  description: '记录 TypeScript、Python 与 agent 工程的学习过程。',
  author: { name: '<作者名>', url: 'https://site.beautymoon.cn/about' },
  siteUrl: process.env.NEXT_PUBLIC_BLOG_URL ?? 'https://blog.beautymoon.cn',
  siteHome: 'https://site.beautymoon.cn',
};
```

Vercel：

- 新建独立项目，Root Directory `apps/blog`，Framework 选 Next.js。
- 仓库根有 `nx.json`，Vercel 会按 Nx monorepo 改写 install/build；新建项目时**显式覆盖**：Install Command 从仓库根安装（`npm ci`），Build Command 用 `npx nx build @beauty/blog` 或 `next build`（以预览环境实测为准）。这一步必须在上线前验证，不能沿用默认值。
- 域名 `blog.beautymoon.cn`（CNAME 到 Vercel）；`NEXT_PUBLIC_BLOG_URL=https://blog.beautymoon.cn`。
- 无 `vercel.json` 需求（无 rewrite、无服务端 API）；若未来加评论，再按 §7 增加 `/api` rewrite。
- 回滚：Vercel 直接回滚到上一个 deployment；内容在 git，回滚即回滚文章。

本地：

```sh
npx nx run @beauty/blog:dev     # http://localhost:3001
npx nx build @beauty/blog       # 产出 apps/blog/out
```

### 5.7 测试与 CI

`npx nx test @beauty/blog -- --run`：

- frontmatter 合法/非法、slug 与文件名不一致、重复 slug、`lab` 缺 `demo`、系列 order 冲突。
- 草稿过滤、标签/系列/归档聚合、上下篇。
- MDX 引用的本地图片/站内链接存在。
- sitemap 含全部公开文章。
- 构建后断言 `out/posts/<slug>/index.html`、`out/feed.xml` 存在。

CI：

- `affected-js` 自动覆盖 `apps/blog`（lint/typecheck/test/build）。
- 根 `vitest.config.mts` 增加 `@beauty/blog` 阈值键。
- `scripts/check-workspace.sh` 增加 `labs` 断言（§6.4）。
- 博客构建**不访问网络**，不依赖案例目录。

## 6. `labs/` 案例隔离

### 6.1 为什么需要围栏

主仓有四道自动发现机制，任何一道被案例触发都会产生依赖/构建影响：

| 机制           | 行为                                                     | 案例风险                                                   |
| -------------- | -------------------------------------------------------- | ---------------------------------------------------------- |
| npm workspaces | 根 `package.json` 只列 `packages/*`、`apps/*`、`tools/*` | `labs/` 不会被安装；但误加 glob/把案例放进 apps 就会 hoist |
| uv workspace   | 根 `pyproject.toml` 显式 members                         | 新增 member 即共享根 `.venv` 与 `uv.lock`                  |
| Nx 项目推断    | `@nx/js/typescript` 会从 `tsconfig.json` 推断项目        | `labs/*/tsconfig.json` 可能进项目图与 affected             |
| 根工具链       | Prettier/ESLint/check-workspace/CI 默认扫全仓            | 案例代码被格式化/lint/门禁误伤                             |

`labs/` 在仓库根，天然不在 npm workspaces 的 `apps|packages|tools` glob 内；但 Nx 推断与根工具链仍需显式关闭。围栏的目标是：**即使有人误放 `tsconfig.json`/`package.json`，案例也不会进入项目图、锁文件、lint 与 CI。**

### 6.2 目录约定

```text
labs/
  <slug>/
    README.md          # 目标、环境、运行命令、结论、日期
    ts/                # 或直接 ts 项目根
      package.json
      package-lock.json
      tsconfig.json
    py/                # 或直接 py 项目根
      pyproject.toml
      uv.lock
      .venv/           # gitignore
    artifacts/         # 截图/GIF，大文件 gitignore
```

- 每个案例独立依赖、独立锁文件、独立 `.venv`，**不互相 import、不共享 node_modules**。
- 端口 4100–4199；数据库名 `lab_<slug>`；Compose 项目名 `lab-<slug>`；环境变量只读自己的 `.env`，不碰主仓 `.env`。
- `labs/` 默认**整体 gitignored**，是一次性本地草稿；发布到博客只复制截图与元数据。

### 6.3 围栏清单（缺一不可）

1. 根 `.gitignore` 增加 `/labs/`；案例内 `.venv/`、`node_modules/`、`artifacts/` 仍各自忽略。
2. 根 `.nxignore` 写入 `labs`（项目推断/缓存/依赖图都不看）。
3. 根 ESLint flat config 增加 `labs/**` 忽略；`.prettierignore` 增加 `/labs`。
4. 不修改根 `package.json` 的 workspaces、根 `pyproject.toml` 的 members/dependencies/sources。
5. 每个案例自带锁文件与 `.venv`；不执行根 `npm install`/`uv sync` 来装案例依赖。
6. 校验脚本断言（§6.4），并接入 `check-workspace.sh` 或 CI setup。

> 说明：`labs/` 被 gitignore 后，案例不会进 clone、不会进安全扫描、不会进 CI；这是「零影响」的关键，而不是靠约定。

### 6.4 校验脚本

新增 `scripts/check-labs.sh`（或并入 `check-workspace.sh`），至少断言：

```sh
# 1. labs 被忽略
git check-ignore -q labs

# 2. labs 无被跟踪文件
test -z "$(git ls-files labs)"

# 3. Nx 项目图不含 labs
! npx nx show projects | grep -q '^labs'

# 4. 根锁文件不含 labs 引用
! grep -q 'labs/' package-lock.json uv.lock

# 5. 根 workspaces/members 未被改写
node -e "const p=require('./package.json'); if((p.workspaces||[]).some(w=>w.startsWith('labs'))) process.exit(1)"
```

任一条失败即 CI 红；这样「临时放进去忘了清」会在合并前暴露。

> 实现：`scripts/check-labs.sh` 已按上述断言落地，并作为 `scripts/check-workspace.sh` 的检查 4/4 接入 CI 的 docs-check job（`labs/` 不存在时跳过 Nx 图检查以保持本地速度）。

### 6.5 生命周期

- **创建**：`mkdir labs/<slug>`，写 README，按语言初始化独立项目；跑通后把截图放 `labs/<slug>/artifacts/`。
- **记录**：博客 frontmatter 写 `demo.path: labs/<slug>`、运行命令、端口、`status`；截图复制到 `apps/blog/public/blog/<slug>/`。
- **退役**：`rm -rf labs/<slug>`，博客 `status` 改 `archived`、移除 `live`；截图留在博客仓，文章不失效。
- **可选发布代码**：如果希望公开源码，推送到独立仓 `beauty-labs` 并在 frontmatter 填 `demo.repo`；**不要**为了让博客引用而把 `labs/` 提交进主仓。

### 6.6 备选（未选）

独立仓 `beauty-labs` 是更彻底的隔离，但用户选择主仓 `labs/` + 围栏。若未来出现「案例需要长期维护/多人协作/公开源码」，再迁到独立仓；迁移成本主要是重写 frontmatter 的 `demo.repo/path`，博客本身不受影响。

## 7. 评论（后置，未排期）

### 7.1 当前状态

- 本期**不写任何评论代码**：不建 `comment-service`、不改 `api-gateway`、不动 `account-service`。
- 博客详情页不预留评论区 DOM；未来上线时加一个客户端组件即可，不影响现有静态页。
- 未来硬约束（D4）：**必须登录才能评论，不做匿名**；公开读。

### 7.2 未来形态（摘要）

- 新服务 `services/comment-service`（:8009，`COMMENT__` 前缀），独立库 `beauty_comment`；公开读、登录写。
- 鉴权复用 account-service 的 OAuth 2.1（授权码 + PKCE），comment-service 做 **BFF**：浏览器只拿 HttpOnly 会话 cookie，access/refresh token 不出服务端。
- 需要 account-service 配套改动（届时单独评估）：新增 `profile:read`、`blog:comments:write` scope；`introspect` 返回可选 `display_name`；预置 `beauty-blog` 客户端。
- 数据：`comments`（`post_slug`、`account_id`、`author_name` 快照、`parent_id`、`depth`、`body_md/body_html`、`status`、`report_count`）、`blog_sessions`、`oauth_flows`、`comment_reports`。
- 审核：`visible/pending/hidden/spam/deleted` 状态机、限流、举报、nh3 白名单清洗、可选 Turnstile。
- 部署：站点加 `/api` rewrite → 网关 → comment-service；Vercel + 1 容器 + 1 库。

完整接口、数据模型与风控细节不在本期展开；启动评论时按那时点的账号服务与部署形态重新评审，不直接照搬旧结论。

### 7.3 启动前检查清单

- [ ] 确认 D3/D4/D6 仍然成立（是否仍要登录、是否接受 account-service 改动）。
- [ ] 评估评论对静态站的影响：`/api` rewrite、Cookie 域、CORS、CSRF。
- [ ] 确认审核人名单与隐私声明（存 `account_id` + 展示名快照，不存邮箱）。
- [ ] 预估成本：后端 3–4 人日 + 前端 2–3 人日 + 部署 1 人日。

## 8. 分期与工作量

| 期   | 内容                                                                              | 人日  | 退出标准                                           |
| ---- | --------------------------------------------------------------------------------- | ----- | -------------------------------------------------- |
| P0   | `apps/blog` 骨架：Nx 项目/依赖/布局、`lib/blog`、列表/详情、3 篇样例、sitemap/RSS | 2–3   | `nx build @beauty/blog` 产出页面与 feed；测试绿    |
| P1   | 体验：标签/系列/归档/TOC/上下篇/OG/校验/重定向                                    | 1.5–2 | 导航可用；坏 frontmatter 构建失败并定位            |
| P2   | `labs/` 围栏：gitignore/.nxignore/ESLint/Prettier/校验脚本 + 文档                 | 0.5–1 | `check-labs.sh` 全绿；`nx show projects` 不含 labs |
| P3   | 部署/CI/观测/回滚演练 + 官网导航/文档                                             | 1     | Vercel 子域上线；官网可跳转；回滚验证              |
| P4   | 评论（未排期）                                                                    | 5–7   | 另行立项；含 account-service 改动                  |
| 合计 | 本期（P0–P3）                                                                     | 5–7   | 博客可发布、案例零污染                             |

> 估算不含内容写作；不含 Pagefind/通知/双链等后续项。

## 9. 风险

| #   | 风险                                                    | 影响         | 缓解                                                             |
| --- | ------------------------------------------------------- | ------------ | ---------------------------------------------------------------- |
| R1  | Vercel 把新项目识别为 Nx monorepo，install/build 被改写 | 首次部署失败 | 新建项目时显式覆盖 install/build；先用预览环境验证               |
| R2  | `next-mdx-remote` 与 Next 16/React 19 兼容问题          | P0 返工      | 先做 1 个 spike；备选 `@next/mdx`/`mdx-bundler`                  |
| R3  | 独立应用复制设计令牌导致视觉漂移                        | 品牌不一致   | 只引 `@beauty/design` 与 `--ha-*` 令牌；抽取 `blog-prose` 排版层 |
| R4  | `labs/` 被误加入 workspace/Nx/锁文件                    | 主仓依赖污染 | §6.3 围栏 + §6.4 校验脚本 + CI 阻断                              |
| R5  | 文章含雇主/客户敏感信息                                 | 合规         | 发布前 checklist；`draft`；私有仓/延迟发布                       |
| R6  | 子域拆分导致 SEO 权重分散                               | 搜索表现     | 官网首页/关于页加入口；博客 canonical 自指；必要时 sitemap 互链  |
| R7  | 评论后置导致未来改造成本                                | 返工         | 本文保留契约摘要；博客详情页不写死评论 DOM                       |
| R8  | 案例截图/内容与代码脱节                                 | 阅读体验     | `status: archived`、日期、运行命令；季度巡检                     |

## 10. 验收标准

- [ ] `npx nx build @beauty/blog` 产出 `/`、`/posts/<slug>`、标签/系列/归档、`feed.xml`、sitemap。
- [ ] 新增文章只需加 1 个 MDX + 图片；坏 frontmatter/重复 slug/缺图构建失败并给出文件路径。
- [ ] `apps/site` 导航与页脚出现「博客」外链，博客 header 可回官网。
- [ ] `labs/` 满足 §6.4 全部断言：gitignored、无跟踪文件、不在 Nx 图、不在根锁文件。
- [ ] 博客构建在断网、`labs/` 不存在时仍成功。
- [ ] 无评论代码、无 account-service 改动、无新增 Python 服务/数据库。
- [ ] Vercel 子域可访问；回滚到上一 deployment 可用；CI affected 能正确覆盖 `@beauty/blog`。
- [ ] 文档：本文、`docs/README.md` 索引、`apps/blog/README.md`、部署说明同步。

## 11. 参考

- [Docusaurus Blog](https://docusaurus.io/docs/blog)：frontmatter、tags、RSS、series 的成熟约定。
- [Astro Content Collections](https://docs.astro.build/en/guides/content-collections/)：构建期 schema 校验的思路。
- [Quartz](https://quartz.jzhao.xyz/)：数字花园的 `stage`、更新时间。
- [Simon Willison's TIL](https://til.simonwillison.net/)：短笔记型学习博客的组织方式。
- [Next.js Static Exports](https://nextjs.org/docs/app/building-your-application/deploying/static-exports)：`output: 'export'` 的能力边界。
