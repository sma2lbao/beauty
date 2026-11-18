/**
 * unified diff 解析:实现与渲染都在 @beauty/design。
 *
 * 这里保留再导出,既有 `@/lib/presentation` barrel 与其消费方零改动。
 */
export {
  diffStats,
  parseUnifiedDiff,
  sideBySide,
  type DiffRow,
  type DiffRowKind,
  type SideBySideRow,
} from '@beauty/design';
