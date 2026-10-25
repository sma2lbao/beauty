import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Filter, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { Dialog, Field, Input, Textarea } from '@beauty/design';

import { RunDetail } from '@/components/console/run-detail';
import {
  ConsoleButton,
  EmptyPanel,
  PageHeader,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  BOARD_COLUMNS,
  RUN_STATUS_LABELS,
  isRunQuiescent,
  relativeTime,
  runStatusTone,
  taskCode,
  toneClasses,
} from '@/lib/presentation';
import type { Run } from '@/lib/runs-api';
import { isUnauthorized, runsApi } from '@/lib/runs-api';
import { type RunFilterKey, useRunBoardStore } from '@/stores/run-board-store';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/query-keys';

const FILTERS: { key: RunFilterKey; label: string }[] = [
  { key: 'all', label: '全部任务' },
  { key: 'active', label: '进行中' },
  { key: 'review', label: '待审核' },
];

/**
 * 任务编排(原 /runs):设计稿《02 任务看板 · 多 Agent 编排》。
 *
 * 状态到列:待启动(pending/planning)、执行中(running)、
 * 待审核(awaiting_approval/awaiting_confirmation/blocked)、已完成(completed/failed)。
 * 卡片点开右侧抽屉,原控制台的能力(审批/追加指令/重试/交付物/事件)都在那里。
 */
export function RunsPage() {
  const queryClient = useQueryClient();
  const selectedRunId = useRunBoardStore((state) => state.selectedRunId);
  const setSelectedRunId = useRunBoardStore((state) => state.setSelectedRun);
  const filter = useRunBoardStore((state) => state.filter);
  const setFilter = useRunBoardStore((state) => state.setFilter);
  const [createOpen, setCreateOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [title, setTitle] = useState('整理本周文档成周报');
  const [goal, setGoal] = useState('汇总本周文档,生成周报草稿');
  const [criteria, setCriteria] = useState('覆盖本周主要进展\n不超过 300 字');

  const runsQuery = useQuery({
    queryKey: queryKeys.runs,
    queryFn: runsApi.list,
    refetchInterval: 5000,
  });
  const createMutation = useMutation({
    mutationFn: () =>
      runsApi.create({
        title: title.trim(),
        goal: goal.trim(),
        acceptance_criteria: criteria
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean),
      }),
    onSuccess: async (run) => {
      setSelectedRunId(run.id);
      setCreateOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });

  const runs = runsQuery.data ?? [];
  const activeCount = runs.filter((run) => !isRunQuiescent(run.status)).length;
  const runningCount = runs.filter((run) => run.status === 'running').length;
  const unauthorized = isUnauthorized(runsQuery.error);

  const visibleColumns = BOARD_COLUMNS.filter((column) => {
    if (filter === 'active') {
      return column.key === 'queued' || column.key === 'running';
    }
    if (filter === 'review') return column.key === 'review';
    return true;
  });

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          title="任务编排"
          subtitle={
            runsQuery.isPending
              ? '正在读取任务…'
              : activeCount +
                ' 个活跃任务 · ' +
                runningCount +
                ' 个执行中 · 共 ' +
                runs.length +
                ' 个任务'
          }
          actions={
            <>
              <div className="relative">
                <ConsoleButton
                  variant="secondary"
                  size="md"
                  aria-expanded={filterOpen}
                  onClick={() => setFilterOpen((value) => !value)}
                  className="h-8"
                >
                  <Filter className="h-3.5 w-3.5" />
                  筛选
                </ConsoleButton>
                {filterOpen ? (
                  <div className="absolute right-0 top-9 z-20 w-40 rounded-lg border border-ha-border bg-ha-sidebar p-1 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
                    {FILTERS.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => {
                          setFilter(item.key);
                          setFilterOpen(false);
                        }}
                        className={cn(
                          'flex h-7 w-full items-center justify-between rounded-md px-2 text-left text-[12px] transition-colors',
                          filter === item.key
                            ? 'bg-[#1c1d1f] text-ha-text'
                            : 'text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text-2',
                        )}
                      >
                        {item.label}
                        {filter === item.key ? (
                          <StatusDot tone="accent" />
                        ) : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => setCreateOpen(true)}
                className={cn(
                  consoleButtonClass({ variant: 'primary', size: 'md' }),
                  'h-8',
                )}
              >
                <Plus className="h-3.5 w-3.5" />
                新建任务
              </button>
            </>
          }
        />

        {unauthorized ? (
          <div className="mx-6 mb-3 rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning sm:mx-8">
            未登录或令牌已失效:请先
            <Link className="mx-1 underline" to="/login">
              登录
            </Link>
            再重新加载。
          </div>
        ) : null}

        {runsQuery.isError && !unauthorized ? (
          <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
            列表加载失败:{runsQuery.error.message}
          </p>
        ) : null}

        <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto px-6 pb-6 sm:px-8">
          {!runsQuery.isPending && runs.length === 0 ? (
            <EmptyPanel
              className="my-auto w-full border-ha-border-strong"
              title="还没有 run。"
              description="任务编排把每次执行当成一条可观测的运行:先规划、放行后执行、交付前审核。新建一个任务开始。"
              action={
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className={consoleButtonClass({
                    variant: 'primary',
                    size: 'md',
                  })}
                >
                  <Plus className="h-3.5 w-3.5" />
                  新建任务
                </button>
              }
            />
          ) : null}

          {runsQuery.isPending
            ? BOARD_COLUMNS.map((column) => (
                <div
                  key={column.key}
                  className="flex min-w-[260px] flex-1 flex-col gap-2.5 rounded-lg border border-ha-border bg-ha-sidebar p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-semibold text-ha-text-2">
                      {column.label}
                    </span>
                    <span className="h-5 w-6 animate-pulse rounded-[5px] bg-ha-panel-2" />
                  </div>
                  {[0, 1].map((key) => (
                    <div
                      key={key}
                      className="h-24 animate-pulse rounded-lg border border-ha-border bg-ha-panel"
                    />
                  ))}
                </div>
              ))
            : null}

          {!runsQuery.isPending && runs.length > 0
            ? visibleColumns.map((column) => {
                const columnRuns = runs.filter((run) =>
                  column.statuses.includes(run.status),
                );
                return (
                  <BoardColumn
                    key={column.key}
                    label={column.label}
                    tone={column.tone}
                    runs={columnRuns}
                    selectedRunId={selectedRunId}
                    onSelect={setSelectedRunId}
                  />
                );
              })
            : null}

          {!runsQuery.isPending && runs.length > 0 && filter === 'review'
            ? null
            : null}
        </div>
      </div>

      {selectedRunId ? (
        <div className="fixed inset-0 z-30 flex bg-black/40 sm:static sm:z-auto sm:bg-transparent">
          <div className="ml-auto flex h-full w-full max-w-[460px] sm:max-w-none">
            <RunDetail
              runId={selectedRunId}
              onSelectRun={setSelectedRunId}
              onClose={() => setSelectedRunId(null)}
            />
          </div>
        </div>
      ) : null}

      <Dialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        title="新建任务"
        description="验收标准每行一条。创建后先规划,等你放行再执行。"
        footer={
          <>
            <ConsoleButton
              variant="secondary"
              size="md"
              onClick={() => setCreateOpen(false)}
            >
              取消
            </ConsoleButton>
            <ConsoleButton
              variant="primary"
              size="md"
              onClick={() => createMutation.mutate()}
              disabled={
                createMutation.isPending || !title.trim() || !goal.trim()
              }
            >
              {createMutation.isPending ? '创建中…' : '创建'}
            </ConsoleButton>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="标题" htmlFor="new-run-title">
            <Input
              id="new-run-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </Field>
          <Field label="目标" htmlFor="new-run-goal">
            <Textarea
              id="new-run-goal"
              rows={3}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
            />
          </Field>
          <Field label="验收标准" htmlFor="new-run-criteria">
            <Textarea
              id="new-run-criteria"
              rows={3}
              value={criteria}
              onChange={(event) => setCriteria(event.target.value)}
            />
          </Field>
          {createMutation.isError ? (
            <p className="text-[12px] text-ha-danger">
              {createMutation.error.message}
            </p>
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}

function BoardColumn({
  label,
  tone,
  runs,
  selectedRunId,
  onSelect,
}: {
  label: string;
  tone: 'neutral' | 'accent' | 'info' | 'success' | 'warning' | 'danger';
  runs: Run[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <div className="flex min-w-[260px] flex-1 flex-col gap-2.5 rounded-lg border border-ha-border bg-ha-sidebar p-3">
      <div className="flex items-center justify-between">
        <span
          className={cn('text-[13px] font-semibold', toneClasses(tone).text)}
        >
          {label}
        </span>
        <span
          className={cn(
            'inline-flex h-5 min-w-6 items-center justify-center rounded-[5px] px-2 font-mono text-[11px] font-medium',
            toneClasses(tone).soft,
          )}
        >
          {runs.length}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
        {runs.length === 0 ? (
          <p className="rounded-md border border-dashed border-ha-border px-3 py-6 text-center text-[11px] text-ha-faint">
            暂无任务
          </p>
        ) : null}
        {runs.map((run) => {
          const runTone = runStatusTone(run.status);
          const running = run.status === 'running';
          const selected = run.id === selectedRunId;
          return (
            <button
              key={run.id}
              type="button"
              onClick={() => onSelect(run.id)}
              className={cn(
                'rounded-lg border p-3 text-left transition-colors',
                running
                  ? 'border-ha-info-border bg-ha-info-bg hover:border-ha-info'
                  : runTone === 'warning'
                    ? 'border-ha-warning-border bg-ha-warning-bg hover:border-ha-warning'
                    : 'border-ha-border bg-ha-panel hover:border-ha-border-strong',
                selected && 'ring-1 ring-ha-accent',
              )}
            >
              <span className="line-clamp-2 block text-[13px] font-medium leading-5 text-ha-text-body">
                {run.title}
              </span>

              <span className="mt-2.5 flex items-center justify-between gap-2">
                <ToneBadge tone={running ? 'info' : runTone}>
                  {run.conversation_id ? '会话任务' : '控制台任务'}
                </ToneBadge>
                <span className="shrink-0 font-mono text-[11px] text-ha-faint">
                  {taskCode(run.id)}
                </span>
              </span>

              {running ? (
                <span className="mt-2.5 block h-[3px] overflow-hidden rounded-full bg-[#0b1b26]">
                  <span className="block h-full w-2/5 animate-pulse rounded-full bg-ha-info" />
                </span>
              ) : null}

              <span
                className={cn(
                  'mt-2.5 block truncate font-mono text-[11px]',
                  running
                    ? 'text-ha-info'
                    : runTone === 'warning'
                      ? 'text-ha-warning'
                      : runTone === 'danger'
                        ? 'text-ha-danger'
                        : 'text-ha-dim',
                )}
              >
                {cardStatusLine(run)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function cardStatusLine(run: Run): string {
  const label = RUN_STATUS_LABELS[run.status];
  const time = relativeTime(run.created_at);
  const reason = run.status_reason
    ? run.status_reason.length > 28
      ? run.status_reason.slice(0, 28) + '…'
      : run.status_reason
    : '';
  if (run.status === 'failed') {
    return '失败 · ' + (reason || time);
  }
  if (run.status === 'blocked') {
    return '已阻塞 · ' + (reason || time);
  }
  if (run.status === 'cancelled') {
    return '已取消 · ' + (reason || time);
  }
  if (run.status === 'awaiting_approval') {
    return '待人工放行 · ' + time;
  }
  if (run.status === 'awaiting_confirmation') {
    return '待确认危险操作 · ' + time;
  }
  if (run.status === 'completed') {
    return '已完成 · ' + time;
  }
  if (run.status === 'running') {
    return (
      RUN_STATUS_LABELS.running +
      (run.rounds > 0 ? ' · 第 ' + run.rounds + ' 轮' : '') +
      ' · ' +
      time
    );
  }
  return label + ' · ' + time;
}
