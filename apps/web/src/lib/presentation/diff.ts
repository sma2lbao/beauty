/**
 * unified diff 解析(实现已迁到 @beauty/console-ui)。
 *
 * 解析与渲染同属展示层,一起迁到共享包;这里保留再导出,
 * 既有 `@/lib/presentation` barrel 与其消费方零改动。
 */
export {
  diffStats,
  parseUnifiedDiff,
  sideBySide,
  type DiffRow,
  type DiffRowKind,
  type SideBySideRow,
} from '@beauty/console-ui';
