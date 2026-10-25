/**
 * run 领域展示映射:状态标签/色调、看板列、任务编号。
 *
 * 只依赖 runs-api 的类型;事件与工具调用的映射见 ./events.ts、./tools.ts。
 */
import type { Run, RunStatus } from '@/lib/runs-api';
import type { Tone } from './format';

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  pending: '待规划',
  planning: '规划中',
  awaiting_approval: '待审批',
  running: '执行中',
  awaiting_confirmation: '待确认危险操作',
  blocked: '已阻塞',
  completed: '已完成',
  failed: '已失败',
  cancelled: '已取消',
};

/** 任务执行阶段标签(token.delta.v1 的 phase):未知阶段由调用方兜底成「执行中」。 */
export const RUN_PHASE_LABELS: Record<string, string> = {
  plan: '规划中',
  execute: '执行中',
  verify: '验收中',
  deliver: '整理交付物',
};

export function runStatusTone(status: RunStatus): Tone {
  switch (status) {
    case 'running':
      return 'info';
    case 'awaiting_approval':
    case 'awaiting_confirmation':
      return 'warning';
    case 'blocked':
    case 'failed':
      return 'danger';
    case 'completed':
      return 'success';
    default:
      return 'neutral';
  }
}

const TERMINAL: readonly RunStatus[] = [
  'completed',
  'failed',
  'blocked',
  'cancelled',
];

export function isRunTerminal(status: RunStatus): boolean {
  return TERMINAL.includes(status);
}

/** 不会自变的终态:轮询可以停(blocked 只能靠人工 resume 改变)。 */
export function isRunQuiescent(status: RunStatus): boolean {
  return isRunTerminal(status);
}

export const BOARD_COLUMNS: {
  key: string;
  label: string;
  tone: Tone;
  statuses: RunStatus[];
}[] = [
  {
    key: 'queued',
    label: '待启动',
    tone: 'neutral',
    statuses: ['pending', 'planning'],
  },
  { key: 'running', label: '执行中', tone: 'info', statuses: ['running'] },
  {
    key: 'review',
    label: '待审核',
    tone: 'warning',
    statuses: ['awaiting_approval', 'awaiting_confirmation', 'blocked'],
  },
  {
    key: 'done',
    label: '已完成',
    tone: 'success',
    statuses: ['completed', 'failed', 'cancelled'],
  },
];

export function boardColumnOf(status: RunStatus): string {
  return (
    BOARD_COLUMNS.find((column) => column.statuses.includes(status))?.key ??
    'queued'
  );
}

/** 任务编号:用 run id 尾部派生出设计稿里的 #TASK-311 样式编号。 */
export function taskCode(runId: string): string {
  const tail = runId
    .replace(/[^a-zA-Z0-9]/g, '')
    .slice(-4)
    .toUpperCase();
  return `#TASK-${tail || '0000'}`;
}

/** 无读取权限时的 fallback:run 上也没存工具记录,只把状态写进副标题。 */
export function runMetaLine(run: Run): string {
  const parts = [RUN_STATUS_LABELS[run.status]];
  if (run.rounds > 0) parts.push(`第 ${run.rounds} 轮`);
  if (run.attempts > 1) parts.push(`第 ${run.attempts} 次尝试`);
  return parts.join(' · ');
}
