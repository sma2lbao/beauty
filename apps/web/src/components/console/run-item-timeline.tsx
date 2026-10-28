/**
 * 统一过程项渲染器(P1-D 事件分类学):消费 `GET /runs/{id}/items` 的投影。
 *
 * 一个 item = run 内一个原子过程项,kind 决定卡片形状:
 * - `plan`:一等过程项,显示「第 N 版」徽标 + Markdown 正文,被 `supersedes`
 *   取代的旧版本折进「旧版本」可折叠组(计划修订 = 新版本,不再是快照回看);
 * - `tool` / `file_change`:工具名/参数/结果摘要,文件变更另显示路径与 sha256 前 8 位;
 * - `mcp_call`:server / tool;`search`:查询与参数;`todo`:复用 RunTodoList 清单。
 *
 * 交互统一:默认折叠结果详情(避免长文本撑爆抽屉),展开后显示结构化 data;
 * `started` 项显示「进行中」,`failed` / `denied` 显示状态徽标。本组件是纯展示
 * 层,数据由 run-detail 轮询 `itemsQuery` 注入,不感知 SSE(未知事件忽略)。
 */
import {
  ChevronDown,
  ChevronRight,
  FileDiff,
  FileText,
  ListTodo,
  Plug,
  Search,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useState } from 'react';

import { Markdown } from '@/components/console/markdown';
import { RunTodoList } from '@/components/console/run-todo-list';
import { ToneBadge } from '@/components/console/ui';
import { formatDuration, toneClasses, type Tone } from '@/lib/presentation';
import type {
  RunItem,
  RunItemKind,
  RunItemStatus,
  RunTodoItem,
} from '@/lib/runs-api';
import { cn } from '@/lib/utils';

/** kind 的展示元信息:标题前缀、图标与身份色由这一处决定。 */
const KIND_META: Record<
  RunItemKind,
  { label: string; icon: LucideIcon; tone: Tone }
> = {
  plan: { label: '计划', icon: FileText, tone: 'accent' },
  tool: { label: '工具', icon: Wrench, tone: 'neutral' },
  file_change: { label: '文件变更', icon: FileDiff, tone: 'warning' },
  mcp_call: { label: 'MCP 调用', icon: Plug, tone: 'info' },
  search: { label: '检索', icon: Search, tone: 'info' },
  todo: { label: '任务清单', icon: ListTodo, tone: 'neutral' },
};

/** 状态徽标:started 相位投影为 running(进行中),终态细粒度到 denied。 */
const STATUS_META: Record<RunItemStatus, { label: string; tone: Tone }> = {
  running: { label: '进行中', tone: 'info' },
  completed: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  denied: { label: '已拒绝', tone: 'warning' },
};

/** 过程项状态:后端给了 status 就用;旧投影缺 status 时按 phase 兜底。 */
function resolveStatus(item: RunItem): RunItemStatus {
  if (item.status) return item.status;
  if (item.phase === 'completed') return 'completed';
  if (item.phase === 'failed') return 'failed';
  return 'running';
}

/* ── data 防御性读取:各相位浅合并,旧数据可能缺字段 ───────────────────── */

function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function booleanOf(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function listOfText(value: unknown): string[] {
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

function todoItemsOf(value: unknown): RunTodoItem[] {
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

/** 计划版本号:优先 data.version;缺省时从 `item_id="<run>:plan:<version>"` 解析。 */
function planVersion(item: RunItem): number | null {
  const fromData = numberOf(item.data.version);
  if (fromData !== null) return fromData;
  const match = /:plan:(\d+)$/.exec(item.item_id);
  return match ? Number(match[1]) : null;
}

/**
 * 计划版本分组:当前版本 = 没有被任何版本 `supersedes` 指到的那个(同层再按
 * 版本号/seq 取最新);其余旧版本按版本升序折进「旧版本」组。
 */
function splitPlanVersions(items: readonly RunItem[]): {
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

/* ── 结构化 data 的展示映射 ───────────────────────────────────────────── */

type DetailRow = { label: string; value: string };
type DetailBlock = { label: string; content: string };

/** 标量字段拼成两列 dl;长文本(arguments/detail/content)单独成块。 */
function detailRows(item: RunItem): DetailRow[] {
  const data = item.data;
  const rows: DetailRow[] = [];
  const push = (label: string, value: string | null) => {
    if (value) rows.push({ label, value });
  };
  const ok = booleanOf(data.ok);
  push('名称', textOf(data.name));
  push('状态', ok === null ? null : ok ? '成功' : '失败');
  push('风险', textOf(data.risk));
  push('决策', textOf(data.decision));
  push('错误码', textOf(data.error_code));
  push('结果字符', numberOf(data.result_chars)?.toString() ?? null);
  push(
    '截断',
    booleanOf(data.truncated) === null
      ? null
      : booleanOf(data.truncated)
        ? '已截断'
        : '完整',
  );
  push('工具调用', textOf(data.tool_call_id));
  push('MCP server', textOf(data.server));
  push('MCP tool', textOf(data.tool));
  if (item.kind === 'plan') {
    push('版本', numberOf(data.version)?.toString() ?? null);
    push('字符', numberOf(data.chars)?.toString() ?? null);
    push('替代版本', textOf(data.supersedes));
  }
  if (item.node) push('节点', item.node);
  if (item.step_id) push('步骤', item.step_id);
  return rows;
}

/** 长文本块:工具参数/结果、检索参数按原样(可滚动)展示。 */
function detailBlocks(item: RunItem): DetailBlock[] {
  const argumentsText = textOf(item.data.arguments);
  const resultText =
    textOf(item.data.detail) ??
    textOf(item.data.output) ??
    textOf(item.data.result);
  const content = textOf(item.data.content);
  const blocks: DetailBlock[] = [];
  if (item.kind === 'plan' && content) {
    blocks.push({ label: '正文', content });
  }
  if (argumentsText) blocks.push({ label: '参数', content: argumentsText });
  if (resultText) blocks.push({ label: '结果', content: resultText });
  return blocks;
}

/* ── 时间线 ───────────────────────────────────────────────────────────── */

export function RunItemTimeline({
  items,
  className,
}: {
  items: readonly RunItem[];
  className?: string;
}) {
  if (items.length === 0) {
    return (
      <p
        data-testid="run-item-empty"
        className={cn(
          'rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim',
          className,
        )}
      >
        还没有过程项记录。计划、工具、文件变更、MCP
        调用、检索与任务清单会随执行出现在这里。
      </p>
    );
  }

  const { current, history } = splitPlanVersions(items);

  return (
    <ol
      aria-label="运行项时间线"
      data-testid="run-item-timeline"
      className={cn('space-y-1.5', className)}
    >
      {items.map((item) => {
        // 计划旧版本折进当前计划卡的「旧版本」组,不再单独占一条。
        if (item.kind === 'plan' && current?.item_id !== item.item_id) {
          return null;
        }
        return (
          <RunItemCard
            key={item.item_id}
            item={item}
            planHistory={item.kind === 'plan' ? history : []}
          />
        );
      })}
    </ol>
  );
}

function RunItemCard({
  item,
  planHistory,
}: {
  item: RunItem;
  planHistory: readonly RunItem[];
}) {
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const kind = KIND_META[item.kind];
  const Icon = kind.icon;
  const status = resolveStatus(item);
  const statusMeta = STATUS_META[status];
  const version = item.kind === 'plan' ? planVersion(item) : null;
  const duration = formatDuration(item.duration_ms);
  const label = `「${item.title}」`;

  return (
    <li
      data-testid={`run-item-${item.item_id}`}
      data-kind={item.kind}
      data-status={status}
      className={cn(
        'overflow-hidden rounded-lg border bg-ha-panel',
        status === 'running' ? 'border-ha-info-border' : 'border-ha-border',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-label={`${open ? '收起' : '展开'}${kind.label}${label}详情`}
        data-testid={`run-item-toggle-${item.item_id}`}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-ha-panel-2"
      >
        <span className="shrink-0 text-ha-faint">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <Icon
          size={13}
          className={cn('shrink-0', toneClasses(kind.tone).text)}
          aria-hidden="true"
        />
        <span className="shrink-0 font-mono text-[10px] tracking-[0.14em] text-ha-faint">
          {kind.label}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ha-text-body">
          {item.title}
        </span>
        {version !== null ? (
          <ToneBadge tone="accent">第 {version} 版</ToneBadge>
        ) : null}
        {item.attempt > 1 ? (
          <span className="shrink-0 font-mono text-[10px] text-ha-faint">
            第 {item.attempt} 次尝试
          </span>
        ) : null}
        {duration ? (
          <span className="shrink-0 font-mono text-[11px] text-ha-faint">
            {duration}
          </span>
        ) : null}
        <ToneBadge tone={statusMeta.tone}>{statusMeta.label}</ToneBadge>
      </button>

      {item.kind === 'plan' ? (
        <PlanBody
          item={item}
          history={planHistory}
          historyOpen={historyOpen}
          onToggleHistory={() => setHistoryOpen((prev) => !prev)}
        />
      ) : null}

      {open ? <RunItemDetail item={item} /> : null}
    </li>
  );
}

/* ── 计划正文 + 旧版本组 ──────────────────────────────────────────────── */

function PlanBody({
  item,
  history,
  historyOpen,
  onToggleHistory,
}: {
  item: RunItem;
  history: readonly RunItem[];
  historyOpen: boolean;
  onToggleHistory: () => void;
}) {
  const content = textOf(item.data.content);
  const truncated = booleanOf(item.data.truncated) === true;

  return (
    <div className="border-t border-ha-border px-3 py-2">
      {content ? (
        <div
          data-testid={`run-plan-content-${item.item_id}`}
          className="max-h-72 overflow-auto rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
        >
          <Markdown content={content} />
        </div>
      ) : (
        <p className="rounded-md border border-dashed border-ha-border px-3 py-2 text-[11px] leading-5 text-ha-dim">
          计划正文缺失,可在「执行步骤」查看节点记录。
        </p>
      )}
      {truncated ? (
        <p className="mt-1.5 font-mono text-[10px] text-ha-warning">
          正文已截断,完整内容见计划快照
        </p>
      ) : null}

      {history.length > 0 ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={onToggleHistory}
            aria-expanded={historyOpen}
            aria-label={`${historyOpen ? '收起' : '展开'}旧版本计划`}
            data-testid="run-plan-history-toggle"
            className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 font-mono text-[11px] text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2"
          >
            {historyOpen ? (
              <ChevronDown size={12} />
            ) : (
              <ChevronRight size={12} />
            )}
            旧版本({history.length})
          </button>
          {historyOpen ? (
            <ol aria-label="计划旧版本" className="mt-1.5 space-y-1.5">
              {history.map((plan) => {
                const oldVersion = planVersion(plan);
                const oldContent = textOf(plan.data.content);
                return (
                  <li
                    key={plan.item_id}
                    data-testid={`run-plan-history-${plan.item_id}`}
                    className="rounded-md border border-ha-border bg-ha-panel-2"
                  >
                    <div className="flex items-center gap-2 px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-[11px] text-ha-dim">
                        {plan.title}
                      </span>
                      {oldVersion !== null ? (
                        <ToneBadge tone="neutral">第 {oldVersion} 版</ToneBadge>
                      ) : null}
                    </div>
                    {oldContent ? (
                      <div className="max-h-48 overflow-auto border-t border-ha-border px-2.5 py-2 opacity-80">
                        <Markdown content={oldContent} />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/* ── 展开后的结构化详情 ───────────────────────────────────────────────── */

function RunItemDetail({ item }: { item: RunItem }) {
  if (item.kind === 'todo') {
    return (
      <div
        data-testid={`run-item-detail-${item.item_id}`}
        className="border-t border-ha-border px-3 py-2"
      >
        <TodoDetail item={item} />
      </div>
    );
  }

  const rows = detailRows(item);
  const blocks = detailBlocks(item);
  const paths = item.kind === 'file_change' ? listOfText(item.data.paths) : [];
  const shaBefore = textOf(item.data.sha256_before);
  const shaAfter = textOf(item.data.sha256_after);

  return (
    <div
      data-testid={`run-item-detail-${item.item_id}`}
      className="space-y-2 border-t border-ha-border px-3 py-2"
    >
      {rows.length > 0 ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px] leading-[17px]">
          {rows.map((row) => (
            <div key={row.label} className="contents">
              <dt className="text-ha-faint">{row.label}</dt>
              <dd className="break-all text-ha-muted">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {paths.length > 0 ? (
        <div>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
            路径
          </span>
          <ul className="mt-1 space-y-0.5">
            {paths.map((path) => (
              <li
                key={path}
                className="break-all font-mono text-[11px] text-ha-text-2"
              >
                {path}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {shaBefore || shaAfter ? (
        <p className="font-mono text-[11px] text-ha-dim">
          sha256: {shortSha(shaBefore)} → {shortSha(shaAfter)}
        </p>
      ) : null}

      {blocks.map((block) => (
        <DetailBlockView key={block.label} block={block} />
      ))}
    </div>
  );
}

function DetailBlockView({ block }: { block: DetailBlock }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
          {block.label}
        </span>
        <span className="font-mono text-[10px] text-ha-faint">
          {block.content.length} 字符
        </span>
      </div>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2 font-mono text-[11px] leading-[17px] text-ha-muted">
        {block.content}
      </pre>
    </div>
  );
}

function TodoDetail({ item }: { item: RunItem }) {
  const todos = todoItemsOf(item.data.todos);
  const revision = numberOf(item.data.revision);
  return (
    <div className="space-y-2">
      {revision !== null ? (
        <p className="font-mono text-[10px] text-ha-faint">
          清单版本 r{revision}
        </p>
      ) : null}
      {todos.length > 0 ? (
        <RunTodoList todos={todos} />
      ) : (
        <p className="text-[11px] leading-5 text-ha-dim">
          这份快照里还没有任务条目。
        </p>
      )}
    </div>
  );
}

/** sha256 只展示前 8 位,够人眼比对又不挤占布局(与 ToolCallCard 一致)。 */
function shortSha(value: string | null): string {
  return value ? value.slice(0, 8) : '—';
}
