/**
 * 运行详情头部摘要:状态徽标/轮次/模型/相对时间、目标、中断原因与父 run 血缘。
 * 只读展示,不含任何操作(操作在 ./run-detail-actions.tsx)。
 */
import { ConsoleButton, RunStatusBadge } from '@/components/console/ui';
import {
  RUN_PARENT_KIND_LABELS,
  RUN_STATUS_LABELS,
  isSubagentRun,
  relativeTime,
} from '@/lib/presentation';

import type { RunDetailModel } from './use-run-detail';

export function RunDetailSummary({
  model,
  onSelectRun,
}: {
  model: RunDetailModel;
  onSelectRun?: (nextRunId: string) => void;
}) {
  const { run, status, reasonLabel } = model;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <RunStatusBadge status={status} />
        <span
          className="font-mono text-[11px] text-ha-dim"
          data-testid="run-status"
        >
          {RUN_STATUS_LABELS[status]}
          {run && run.rounds > 0 ? ' · 第 ' + run.rounds + ' 轮' : ''}
        </span>
        {run?.model ? (
          <span
            className="font-mono text-[11px] text-ha-muted"
            data-testid="run-model"
          >
            模型 {run.model}
          </span>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-ha-faint">
          {relativeTime(run?.created_at)}
        </span>
      </div>
      <p className="mt-2 text-[12px] leading-5 text-ha-muted">{run?.goal}</p>
      {run?.status_reason ? (
        <p className="mt-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2 text-[12px] leading-5 text-ha-text-2">
          {reasonLabel}:{run.status_reason}
        </p>
      ) : null}
      {run?.parent_run_id ? (
        isSubagentRun(run) ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <p
              data-testid="run-parent-subagent"
              className="min-w-0 font-mono text-[11px] text-ha-dim"
            >
              由父 run {run.parent_run_id.slice(0, 8)}…
              {` 委派的${RUN_PARENT_KIND_LABELS.subagent}`}
              {run.agent_name ? `「${run.agent_name}」` : ''}
            </p>
            {onSelectRun ? (
              <ConsoleButton
                variant="secondary"
                size="sm"
                data-testid="run-parent-open"
                onClick={() => onSelectRun(run.parent_run_id ?? '')}
              >
                返回父 run
              </ConsoleButton>
            ) : null}
          </div>
        ) : (
          <p className="mt-2 font-mono text-[11px] text-ha-dim">
            由 run {run.parent_run_id.slice(0, 8)}… 重试而来(来源 run
            的历史与失败原因原样保留)
          </p>
        )
      ) : null}
    </>
  );
}
