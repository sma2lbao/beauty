/**
 * 记忆展示文案与统计卡定义:纯常量,不含 React 依赖。
 */
import type { MemoryKind, MemoryScope } from '@/lib/memory-api';

/** 记忆类型的中文名。 */
export const KIND_LABELS: Record<MemoryKind, string> = {
  note: '笔记',
  preference: '偏好',
  fact: '事实',
  procedure: '流程',
};

/** 类型徽章颜色。 */
export const KIND_COLORS: Record<
  MemoryKind,
  'neutral' | 'primary' | 'success' | 'info'
> = {
  note: 'neutral',
  preference: 'primary',
  fact: 'success',
  procedure: 'info',
};

export const SCOPE_LABELS: Record<MemoryScope, string> = {
  tenant: '租户级',
  workspace: '工作区级',
};

/** 统计卡定义(后端 GET /memories/stats 的四个字段)。 */
export const STAT_ITEMS = [
  { key: 'total', label: '记忆总数', hint: '租户内全部记忆' },
  { key: 'active', label: '有效', hint: '未过期、可被检索' },
  { key: 'pinned', label: '置顶', hint: '始终注入上下文' },
  { key: 'expired', label: '已过期', hint: '可清理' },
] as const;
