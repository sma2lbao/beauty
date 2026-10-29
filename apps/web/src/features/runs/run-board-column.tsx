/**
 * 看板单列:列头(名称 + 计数)与任务卡;卡片底部状态行由 cardStatusLine 生成。
 */
import {
  RUN_PARENT_KIND_LABELS,
  RUN_STATUS_LABELS,
  isSubagentRun,
  relativeTime,
  runStatusTone,
  taskCode,
  toneClasses,
} from '@/lib/presentation';
import type { Tone } from '@/lib/presentation';
import { ToneBadge } from '@/components/console/ui';
import type { Run } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

export function RunBoardColumn({
  label,
  tone,
  runs,
  selectedRunId,
  onSelect,
}: {
  label: string;
  tone: Tone;
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
                <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <ToneBadge tone={running ? 'info' : runTone}>
                    {run.conversation_id ? '会话任务' : '控制台任务'}
                  </ToneBadge>
                  {isSubagentRun(run) ? (
                    <span data-testid="run-subagent-badge">
                      <ToneBadge tone="accent" mono={false}>
                        {RUN_PARENT_KIND_LABELS.subagent}
                      </ToneBadge>
                    </span>
                  ) : null}
                </span>
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

/** 卡片底部状态行:失败/阻塞/取消带原因截断,其余按状态给时间或轮次。 */
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
  if (run.status === 'awaiting_input') {
    return '等待你回答 · ' + time;
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
