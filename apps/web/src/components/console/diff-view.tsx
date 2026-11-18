/**
 * Diff / 代码查看器(实现已迁到 @beauty/design)。
 *
 * 保留该路径作为应用内适配层:pages/changes.tsx、tool-call-card.tsx、
 * artifact-preview.tsx 的既有引用零改动;新代码可直接从 @beauty/design 引用。
 * 迁入 design 的原因:样式要回到纯 CSS + bd- 令牌,不能依赖宿主的 Tailwind。
 */
export {
  CodePreview,
  DiffRows,
  DiffViewer,
  SideBySideRows,
} from '@beauty/design';
