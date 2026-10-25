/**
 * 执行步骤时间线(07-run-step-model S1):消费 `GET /runs/{id}/steps` 的投影。
 *
 * 与工具调用清单的分工:工具调用回答「调了什么」,步骤回答「哪个节点、第几次
 * 尝试、第几轮、花了多久」——排障定位锚点在这一层。步骤不进 SSE,所以是轮询
 * 快照:执行中的节点要等它跑完(一次 invoke 结束)才会出现 `running` 之后的终值。
 */
import { Check, Circle, Loader2, X } from 'lucide-react';

import {
  STEP_NODE_LABELS,
  STEP_STATUS_LABELS,
  formatStepDuration,
} from '@/lib/presentation';
import type { RunStep, StepStatus } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

export function RunStepTimeline({
  steps,
  className,
}: {
  steps: readonly RunStep[];
  className?: string;
}) {
  if (steps.length === 0) {
    return (
      <p
        className={cn(
          'rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim',
          className,
        )}
      >
        还没有步骤记录。每个节点(规划/执行/工具/验收/交付)跑完会出现在这里。
      </p>
    );
  }

  return (
    <ol
      aria-label="执行步骤时间线"
      data-testid="run-step-timeline"
      className={cn('space-y-1.5', className)}
    >
      {steps.map((step) => {
        const duration = formatStepDuration(step.duration_ms);
        const meta = [
          step.phase,
          STEP_STATUS_LABELS[step.status] ?? step.status,
          step.attempt > 1 ? `第 ${step.attempt} 次尝试` : '',
          step.round > 1 ? `第 ${step.round} 轮` : '',
          duration,
          `#${step.seq}`,
        ]
          .filter((part) => part !== '')
          .join(' · ');
        return (
          <li
            key={step.step_id}
            className="flex items-start gap-2.5 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2.5"
          >
            <span className="mt-0.5 shrink-0">
              <StepIcon status={step.status} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-medium text-ha-text-body">
                {STEP_NODE_LABELS[step.node] ?? step.node}
              </span>
              <span className="mt-0.5 block truncate font-mono text-[11px] text-ha-dim">
                {meta}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === 'running') {
    return <Loader2 className="h-4 w-4 animate-spin text-ha-info" />;
  }
  if (status === 'completed') {
    return (
      <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-success bg-ha-success-bg">
        <Check className="h-2.5 w-2.5 text-ha-success" />
      </span>
    );
  }
  if (status === 'failed' || status === 'blocked') {
    return (
      <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-danger bg-ha-danger-bg">
        <X className="h-2.5 w-2.5 text-ha-danger" />
      </span>
    );
  }
  return <Circle aria-hidden="true" className="h-4 w-4 text-ha-ghost" />;
}
