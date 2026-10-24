/**
 * Diff / 代码查看器(展示实现已迁到 @beauty/console-ui)。
 *
 * 保留该路径作为应用内适配层:pages/changes.tsx、tool-call-card.tsx、
 * artifact-preview.tsx 的既有引用零改动;新代码可直接从 @beauty/console-ui 引用。
 * 迁出原因:desktop 渲染进程与 web 控制台都要用同一套 diff/代码展示。
 */
export {
  CodePreview,
  DiffRows,
  DiffViewer,
  SideBySideRows,
} from '@beauty/console-ui';
