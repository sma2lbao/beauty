# @beauty/design

beauty monorepo 的 Web 端 React UI 组件库,沿用 shadcn/ui 的令牌与设计语言:组件样式是**普通 CSS** + `bd-` 前缀的 BEM 语义类名(`.bd-button`、`.bd-button--primary`),颜色/圆角/字体只读 `--bd-*` 令牌(美月 Beauty Moon,亮暗双主题),组件源码里不出现 Tailwind 工具类;TypeScript 类型完备。

## 消费形态

| 场景 | 解析到 | 说明 |
| --- | --- | --- |
| 仓库内(workspace 包) | `exports["."]["@beauty/source"]` → `src/index.ts` | 消费方打包器直接编译源码,改组件即时生效,**不需要先构建**。工具链需声明同名条件:TS `customConditions: ["@beauty/source"]`、Vite `resolve.conditions`、Rspress `conditionNames`(见 `apps/web` 与文档站配置) |
| 仓库外 / 发布 | `main` / `module` / `types` → `dist` | 由 `nx build @beauty/design` 产出:`tsc` 原样 emit 合法 ESM(相对导入带真实 `.js` 后缀),`node` 也能直接 `import` |

两条路径共用同一份源码:条件命中就用源码,否则落到 `dist`,所以"仓库内开发"与"打包发布"互不影响。

## 样式的两条消费路径

按宿主是否使用 Tailwind 二选一。

### A. 宿主不引入 Tailwind(任何打包器)

在应用入口引入样式入口即可(`@import` 链由你的打包器内联,不需要额外构建步骤):

```tsx
// 应用入口(如 src/main.tsx)
import '@beauty/design/styles.css';
```

`styles.css` 是一个 CSS 入口:令牌 + 全部组件 CSS,**零 Tailwind**——没有 preflight,也不会改动宿主的任何全局样式。链内的 `@import` 由你的打包器内联(Vite / webpack / Next / Rspack);宿主可以是纯 CSS、CSS Modules、CSS-in-JS 或任何其它方案。

### B. 宿主使用 Tailwind CSS v4

```css
/* src/index.css */
@import 'tailwindcss';
@import '@beauty/design/tailwind.css';
```

这个入口在令牌与组件 CSS 之外,多一层 `@theme` 映射:把 `--bd-*` 暴露成 Tailwind 主题变量,于是你可以直接用 `bg-primary`、`text-muted-foreground`、`rounded-md` 等工具类写业务样式,并自动跟随令牌换肤。

### 覆写组件样式

组件样式是未分层、单类(0,1,0)选择器,宿主在库之后引入、或提高特异性即可覆盖:

```css
.page .my-btn { padding: 0 24px; }
```

需要绝对优先级(含盖过 Tailwind 工具类)时用 `@layer` 让位:

```css
@layer theme, base, bd, components, utilities;
@import '@beauty/design/styles.css' layer(bd);
```

完整的类名表与三种覆写写法见文档站「样式与覆写」。

### 暗色模式

在任意祖先节点(通常是 `html`)添加 `dark` class 即切换。

### 主题定制

覆盖 `--bd-*` 变量即可(见文档站「主题定制」)。不要在宿主里依赖 `--primary`、`--background` 这类未加前缀的名字:那是宿主的命名空间,组件库既不读也不写。

## 文档站(Rspress)本地开发

```bash
npm run docs:dev -w @beauty/design
```

## 构建(发布产物)

```bash
nx build @beauty/design   # 产出 dist:合法 ESM + 类型声明,供仓库外消费/发布
```

`package.json` 的 `files` 已包含 `dist` 与 `src`(后者供 `@beauty/source` 条件和两个 CSS 入口使用)——注意 `.gitignore` 里有 `dist`,没有这个白名单时 `npm pack` 会把 `dist` 一起排除掉。发布前把 `private` 改为 `false` 即可。

## 单元测试

```bash
nx test @beauty/design
```
