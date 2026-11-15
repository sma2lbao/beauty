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
  account: '账号级',
  workspace: '工作区级',
};

/** 记忆来源的中文名(未知来源原样展示)。 */
export const SOURCE_LABELS: Record<string, string> = {
  manual: '手动写入',
  chat: '对话抽取',
  run: '任务抽取',
  tool: '工具写入',
};

/** 注入模式:always 表示条目常驻核心块。 */
export const INJECTION_LABELS: Record<string, string> = {
  retrieved: '按需召回',
  always: '常驻注入',
};

/** 统计卡定义(后端 GET /memories/stats 的六个字段)。 */
export const STAT_ITEMS = [
  { key: 'total', label: '记忆总数', hint: '账号内全部记忆' },
  { key: 'active', label: '有效', hint: '未过期、未被替代' },
  { key: 'pinned', label: '置顶', hint: '排序靠前' },
  { key: 'expired', label: '已过期', hint: '可清理' },
  { key: 'superseded', label: '已失效', hint: '被新事实替代' },
  { key: 'pending_reviews', label: '待复核', hint: '低置信度候选' },
] as const;
