# @beauty/console-ui

apps/web 控制台与 apps/desktop 渲染进程共用的展示层(纯展示、props 驱动)。

- `cn`:Tailwind 类名合并(三端唯一实现,留在本包)
- `tone` / `toneClasses`:语义色(实现与类型都在 `@beauty/design`,此处按旧名再导出)
- `StatusDot` / `StatusText` / `ToneBadge` / `StatusPill`:状态基元,再导出 `@beauty/design` 的实现;
  `EmptyPanel` 仍是本包独有的控制台空态
- `diff` / `diff-view`:unified diff 解析与 Diff/代码查看器,再导出 `@beauty/design`
- `@beauty/console-ui/markdown`:Markdown 渲染,再导出 `@beauty/design/markdown`
  (单独子入口,避免桌面端带入 react-markdown)

实现已逐步收敛到 `@beauty/design`(纯 CSS + `bd-` 类 + `--bd-*` 令牌),
本包只保留控制台旧路径与 Tailwind 相关的 `cn`;新代码优先直接引用 `@beauty/design`。

宿主需在 Tailwind 入口里登记本包源码,否则 `ha-*` 工具类不会被生成:

```css
@source '../../../packages/console-ui/src';
```
