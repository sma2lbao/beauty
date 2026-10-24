# @beauty/console-ui

apps/web 控制台与 apps/desktop 渲染进程共用的展示层(纯展示、props 驱动)。

- `cn`:Tailwind 类名合并(三端唯一实现)
- `tone` / `toneClasses`:语义色 → Tailwind 类
- `StatusDot` / `StatusText` / `ToneBadge` / `StatusPill` / `EmptyPanel`:状态基元
- `diff` / `diff-view`:unified diff 解析与 Diff/代码查看器
- `@beauty/console-ui/markdown`:Markdown 渲染(单独子入口,避免桌面端带入 react-markdown)

宿主需在 Tailwind 入口里登记本包源码,否则 `ha-*` 工具类不会被生成:

```css
@source '../../../packages/console-ui/src';
```
