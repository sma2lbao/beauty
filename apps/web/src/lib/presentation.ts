/**
 * 控制台展示层:状态 → 文案/色调、时间/字节格式化、工具调用与 Diff 解析。
 *
 * 设计稿(ardot《Harness Agent Web 控制台》)把状态色限定在小面积标签上:
 * 信息 #2EA7FF、成功 #13DDC4、警告 #FFB454、危险 #FF6B81;
 * 这里把后端状态映射成同一套语义,页面只消费 tone,不再各自写颜色。
 */
import type { Run, RunEvent, RunStatus } from '@/lib/runs-api';

export type Tone =
  | 'neutral'
  | 'accent'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger';

type ToneClasses = {
  /** 小面积底(标签/状态点背景) */
  solid: string;
  /** 同色 12% 底 + 同色文字 */
  soft: string;
  /** 同色描边(选中卡片/运行中步骤) */
  outline: string;
  /** 纯文字色 */
  text: string;
  /** 圆点 */
  dot: string;
};

function toneClasses(tone: Tone): ToneClasses {
  switch (tone) {
    case 'accent':
      return {
        solid: 'bg-ha-accent text-white',
        soft: 'bg-ha-accent/15 text-ha-accent-soft',
        outline: 'border-ha-accent/60 bg-ha-accent/10',
        text: 'text-ha-accent-soft',
        dot: 'bg-ha-accent-soft',
      };
    case 'info':
      return {
        solid: 'bg-ha-info text-[#04121c]',
        soft: 'bg-ha-info-bg text-ha-info',
        outline: 'border-ha-info-border bg-ha-info-bg',
        text: 'text-ha-info',
        dot: 'bg-ha-info',
      };
    case 'success':
      return {
        solid: 'bg-ha-success text-[#04140f]',
        soft: 'bg-ha-success-bg text-ha-success',
        outline: 'border-ha-success-border bg-ha-success-bg',
        text: 'text-ha-success',
        dot: 'bg-ha-success',
      };
    case 'warning':
      return {
        solid: 'bg-ha-warning text-[#1a1103]',
        soft: 'bg-ha-warning-bg text-ha-warning',
        outline: 'border-ha-warning-border bg-ha-warning-bg',
        text: 'text-ha-warning',
        dot: 'bg-ha-warning',
      };
    case 'danger':
      return {
        solid: 'bg-ha-danger text-[#1a070c]',
        soft: 'bg-ha-danger-bg text-ha-danger',
        outline: 'border-ha-danger-border bg-ha-danger-bg',
        text: 'text-ha-danger',
        dot: 'bg-ha-danger',
      };
    default:
      return {
        solid: 'bg-ha-panel-2 text-ha-text-2',
        soft: 'bg-ha-panel-2 text-ha-muted',
        outline: 'border-ha-border bg-ha-panel',
        text: 'text-ha-muted',
        dot: 'bg-ha-ghost',
      };
  }
}

export { toneClasses };

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  pending: '待规划',
  planning: '规划中',
  awaiting_approval: '待审批',
  running: '执行中',
  awaiting_confirmation: '待确认写操作',
  blocked: '已阻塞',
  completed: '已完成',
  failed: '已失败',
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

const TERMINAL: readonly RunStatus[] = ['completed', 'failed', 'blocked'];

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
    statuses: ['completed', 'failed'],
  },
];

export function boardColumnOf(status: RunStatus): string {
  return (
    BOARD_COLUMNS.find((column) => column.statuses.includes(status))?.key ??
    'queued'
  );
}

/** 相对时间:设计稿里元信息统一用「运行中 · 12 分钟前」这种写法。 */
export function relativeTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  const diff = Date.now() - timestamp;
  const abs = Math.abs(diff);
  const format = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });
  if (abs < 60_000) return '刚刚';
  if (abs < 3_600_000) return format.format(-Math.round(diff / 60_000), 'minute');
  if (abs < 86_400_000) {
    return format.format(-Math.round(diff / 3_600_000), 'hour');
  }
  if (abs < 7 * 86_400_000) {
    return format.format(-Math.round(diff / 86_400_000), 'day');
  }
  return new Date(timestamp).toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  });
}

export function clockTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 任务编号:用 run id 尾部派生出设计稿里的 #TASK-311 样式编号。 */
export function taskCode(runId: string): string {
  const tail = runId.replace(/[^a-zA-Z0-9]/g, '').slice(-4).toUpperCase();
  return `#TASK-${tail || '0000'}`;
}

export function asText(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value ? value : fallback;
}

/** 从工具参数 JSON 里提炼一行摘要(read_file 的路径、发信的收件人…)。 */
export function summarizeArguments(argumentsText: string): string {
  try {
    const parsed = JSON.parse(argumentsText || '{}') as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.length === 0) return '';
      return entries
        .slice(0, 2)
        .map(([key, value]) => {
          const text =
            typeof value === 'string' ? value : JSON.stringify(value) ?? '';
          const short = text.length > 48 ? `${text.slice(0, 48)}…` : text;
          return `${key}=${short}`;
        })
        .join(' · ');
    }
    return String(parsed);
  } catch {
    return argumentsText.length > 64
      ? `${argumentsText.slice(0, 64)}…`
      : argumentsText;
  }
}

export type ToolCall = {
  seq: number;
  name: string;
  arguments: string;
  ok: boolean;
  detail: string;
  occurredAt?: string;
};

export function toolCallsFrom(events: readonly RunEvent[]): ToolCall[] {
  return events
    .filter((event) => event.type === 'run.tool.called.v1')
    .map((event) => ({
      seq: event.seq,
      name: asText(event.payload.name, 'tool'),
      arguments: asText(event.payload.arguments),
      ok: event.payload.ok !== false,
      detail: asText(event.payload.detail),
      occurredAt: event.occurred_at,
    }));
}

/** 事件流 → 对话里的一行系统播报(只做展示映射,不改状态语义)。 */
export function eventLine(
  event: RunEvent,
): { text: string; tone: Tone } | null {
  const payload = event.payload ?? {};
  switch (event.type) {
    case 'run.created.v1':
      return {
        text: `已建立任务《${asText(payload.title, '未命名')}》,开始规划…`,
        tone: 'neutral',
      };
    case 'run.planned.v1':
      return {
        text: `计划已就绪(第 ${payload.attempt ?? 1} 次尝试),等你确认后执行。`,
        tone: 'neutral',
      };
    case 'run.approved.v1':
      return { text: '已放行,开始执行。', tone: 'success' };
    case 'run.confirmation.required.v1':
      return {
        text: '有写操作需要你确认(例如发信),确认后才会执行。',
        tone: 'warning',
      };
    case 'run.confirmation.received.v1':
      return {
        text: `写操作确认:${asText(payload.action, '已回复')}`,
        tone: 'neutral',
      };
    case 'run.instruction.queued.v1':
      return { text: '已记下你的补充,会在下一个边界并入执行。', tone: 'neutral' };
    case 'run.instruction.consumed.v1':
      return { text: '补充指令已并入本次执行。', tone: 'neutral' };
    case 'run.rejected.v1':
      return {
        text: `计划被拒绝:${asText(payload.reason, '未填原因')}`,
        tone: 'warning',
      };
    case 'run.resumed.v1':
      return { text: '已重新进入审批。', tone: 'neutral' };
    case 'run.retry.requested.v1':
      return { text: '已另起一跑重试(原任务历史保留)。', tone: 'neutral' };
    case 'run.failed.v1':
      return {
        text: `执行失败(${asText(payload.stage, '未知阶段')}):${asText(payload.reason, '未知原因')}`,
        tone: 'danger',
      };
    case 'run.blocked.v1':
      return {
        text: `任务中断:${asText(payload.reason, '需要人工处理')}`,
        tone: 'warning',
      };
    case 'run.delivered.v1':
      return {
        text: `已交付《${asText(payload.title, '成品')}》。`,
        tone: 'success',
      };
    case 'run.completed.v1':
      return { text: '任务完成 ✅', tone: 'success' };
    default:
      return null;
  }
}

/** 无读取权限时的 fallback:run 上也没存工具记录,只把状态写进副标题。 */
export function runMetaLine(run: Run): string {
  const parts = [RUN_STATUS_LABELS[run.status]];
  if (run.rounds > 0) parts.push(`第 ${run.rounds} 轮`);
  if (run.attempts > 1) parts.push(`第 ${run.attempts} 次尝试`);
  return parts.join(' · ');
}

export type DiffRowKind = 'hunk' | 'add' | 'del' | 'context';

export type DiffRow = {
  kind: DiffRowKind;
  text: string;
  oldLine: number | null;
  newLine: number | null;
};

/**
 * 解析 unified diff(---/+++/@@ 或仅 @@ 的片段)。
 * 不是 diff 时返回 null,调用方回退成普通代码预览。
 */
export function parseUnifiedDiff(content: string): DiffRow[] | null {
  const lines = content.split('\n');
  const looksLikeDiff =
    lines.some((line) => line.startsWith('@@')) ||
    (lines.some((line) => line.startsWith('--- ')) &&
      lines.some((line) => line.startsWith('+++ ')));
  if (!looksLikeDiff) return null;

  const rows: DiffRow[] = [];
  let oldLine = 0;
  let newLine = 0;
  for (const line of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: 'hunk', text: line, oldLine: null, newLine: null });
      continue;
    }
    if (
      line.startsWith('diff ') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('new file') ||
      line.startsWith('deleted file')
    ) {
      rows.push({ kind: 'hunk', text: line, oldLine: null, newLine: null });
      continue;
    }
    if (line.startsWith('+')) {
      rows.push({ kind: 'add', text: line, oldLine: null, newLine });
      newLine += 1;
      continue;
    }
    if (line.startsWith('-')) {
      rows.push({ kind: 'del', text: line, oldLine, newLine: null });
      oldLine += 1;
      continue;
    }
    rows.push({ kind: 'context', text: line, oldLine, newLine });
    oldLine += 1;
    newLine += 1;
  }
  return rows;
}

export type SideBySideRow = {
  kind: DiffRowKind;
  left: DiffRow | null;
  right: DiffRow | null;
};

/** 并排视图:把连续的删除/新增配对到同一行,上下文行两侧都显示。 */
export function sideBySide(rows: readonly DiffRow[]): SideBySideRow[] {
  const result: SideBySideRow[] = [];
  let index = 0;
  while (index < rows.length) {
    const row = rows[index];
    if (row.kind === 'hunk' || row.kind === 'context') {
      result.push({ kind: row.kind, left: row, right: row });
      index += 1;
      continue;
    }
    const removed: DiffRow[] = [];
    const added: DiffRow[] = [];
    while (index < rows.length && rows[index].kind === 'del') {
      removed.push(rows[index]);
      index += 1;
    }
    while (index < rows.length && rows[index].kind === 'add') {
      added.push(rows[index]);
      index += 1;
    }
    const length = Math.max(removed.length, added.length);
    for (let offset = 0; offset < length; offset += 1) {
      result.push({
        kind: removed[offset] ? 'del' : 'add',
        left: removed[offset] ?? null,
        right: added[offset] ?? null,
      });
    }
  }
  return result;
}

/** 计数:页面顶栏要显示 +N -M。不是 diff 时返回 null。 */
export function diffStats(
  content: string,
): { added: number; removed: number } | null {
  const rows = parseUnifiedDiff(content);
  if (!rows) return null;
  return {
    added: rows.filter((row) => row.kind === 'add').length,
    removed: rows.filter((row) => row.kind === 'del').length,
  };
}
