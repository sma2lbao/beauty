/**
 * 运行项(RunItem)的展示元信息与 data 防御性解析。
 *
 * 事件投影的 data 按相位浅合并:started 相位只有前半段字段、旧数据也可能
 * 缺键,所以解析统一走这里的防御性读取函数;组件只消费归一化后的展示对象。
 */
import {
  Bot,
  FileDiff,
  FileText,
  ListTodo,
  Plug,
  Search,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { RUN_STATUS_LABELS, type Tone } from '@/lib/presentation';
import type {
  RunItem,
  RunItemKind,
  RunItemStatus,
  RunStatus,
  RunTodoItem,
} from '@/lib/runs-api';

/** kind 的展示元信息:标题前缀、图标与身份色由这一处决定。 */
export const KIND_META: Record<
  RunItemKind,
  { label: string; icon: LucideIcon; tone: Tone }
> = {
  plan: { label: '计划', icon: FileText, tone: 'accent' },
  tool: { label: '工具', icon: Wrench, tone: 'neutral' },
  file_change: { label: '文件变更', icon: FileDiff, tone: 'warning' },
  mcp_call: { label: 'MCP 调用', icon: Plug, tone: 'info' },
  search: { label: '检索', icon: Search, tone: 'info' },
  todo: { label: '任务清单', icon: ListTodo, tone: 'neutral' },
  subagent: { label: '子代理', icon: Bot, tone: 'accent' },
};

/** 状态徽标:started 相位投影为 running(进行中),终态细粒度到 denied。 */
export const STATUS_META: Record<RunItemStatus, { label: string; tone: Tone }> =
  {
    running: { label: '进行中', tone: 'info' },
    completed: { label: '已完成', tone: 'success' },
    failed: { label: '失败', tone: 'danger' },
    denied: { label: '已拒绝', tone: 'warning' },
  };

/** 过程项状态:后端给了 status 就用;旧投影缺 status 时按 phase 兜底。 */
export function resolveStatus(item: RunItem): RunItemStatus {
  if (item.status) return item.status;
  if (item.phase === 'completed') return 'completed';
  if (item.phase === 'failed') return 'failed';
  return 'running';
}

/* ── data 防御性读取:各相位浅合并,旧数据可能缺字段 ───────────────────── */

export function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

export function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function booleanOf(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function listOfText(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is string => typeof entry === 'string' && entry !== '',
  );
}

const TODO_STATUSES: readonly RunTodoItem['status'][] = [
  'pending',
  'in_progress',
  'completed',
];

export function todoItemsOf(value: unknown): RunTodoItem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return [];
    const record = entry as Record<string, unknown>;
    const content = textOf(record.content);
    const status = record.status;
    if (!content) return [];
    if (typeof status !== 'string') return [];
    if (!TODO_STATUSES.includes(status as RunTodoItem['status'])) return [];
    return [{ content, status: status as RunTodoItem['status'] }];
  });
}

/** data 里的 run 状态字符串 → 联合类型;未知值按「没有子 run 状态」处理。 */
function runStatusOf(value: unknown): RunStatus | null {
  const text = textOf(value);
  if (!text || !(text in RUN_STATUS_LABELS)) return null;
  return text as RunStatus;
}

/** 子代理过程项里我们用到的字段(全部防御性读取,started 相位缺后一半)。 */
export type SubagentItem = {
  childRunId: string | null;
  agentName: string | null;
  childStatus: RunStatus | null;
  summary: string | null;
  reason: string | null;
  tools: string[];
  timeoutSeconds: number | null;
  usage: SubagentUsage | null;
};

export type SubagentUsage = {
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
};

function subagentUsageOf(value: unknown): SubagentUsage | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const calls = numberOf(record.calls);
  const totalTokens = numberOf(record.total_tokens);
  if (calls === null && totalTokens === null) return null;
  return {
    calls: calls ?? 0,
    promptTokens: numberOf(record.prompt_tokens) ?? 0,
    completionTokens: numberOf(record.completion_tokens) ?? 0,
    totalTokens: totalTokens ?? 0,
  };
}

/**
 * 子代理过程项 → 展示对象。started 相位只有 `child_run_id/agent_name/title/
 * tools/timeout_seconds`,completed 相位才补 `child_status/summary/usage/reason`;
 * 两种相位都可能缺字段,缺了就不渲染对应块。
 */
export function subagentOf(item: RunItem): SubagentItem | null {
  if (item.kind !== 'subagent') return null;
  const data = item.data;
  return {
    childRunId: textOf(data.child_run_id),
    agentName: textOf(data.agent_name),
    childStatus: runStatusOf(data.child_status),
    summary: textOf(data.summary),
    reason: textOf(data.reason),
    tools: listOfText(data.tools),
    timeoutSeconds: numberOf(data.timeout_seconds),
    usage: subagentUsageOf(data.usage),
  };
}

/** 计划版本号:优先 data.version;缺省时从 `item_id="<run>:plan:<version>"` 解析。 */
export function planVersion(item: RunItem): number | null {
  const fromData = numberOf(item.data.version);
  if (fromData !== null) return fromData;
  const match = /:plan:(\d+)$/.exec(item.item_id);
  return match ? Number(match[1]) : null;
}

/**
 * 计划版本分组:当前版本 = 没有被任何版本 `supersedes` 指到的那个(同层再按
 * 版本号/seq 取最新);其余旧版本按版本升序折进「旧版本」组。
 */
export function splitPlanVersions(items: readonly RunItem[]): {
  current: RunItem | null;
  history: RunItem[];
} {
  const plans = items.filter((item) => item.kind === 'plan');
  if (plans.length === 0) return { current: null, history: [] };
  const superseded = new Set<string>();
  for (const plan of plans) {
    const supersedes = textOf(plan.data.supersedes);
    if (supersedes) superseded.add(supersedes);
  }
  const rank = (item: RunItem) => planVersion(item) ?? item.seq;
  const live = plans.filter((plan) => !superseded.has(plan.item_id));
  const pool = live.length > 0 ? live : plans;
  let current = pool[0];
  for (const candidate of pool) {
    if (rank(candidate) >= rank(current)) current = candidate;
  }
  const history = plans
    .filter((plan) => plan.item_id !== current.item_id)
    .sort((a, b) => rank(a) - rank(b));
  return { current, history };
}
