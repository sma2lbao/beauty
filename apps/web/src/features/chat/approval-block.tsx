import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

import { Markdown } from '@/components/console/markdown';
import { ConsoleButton, ToneBadge } from '@/components/console/ui';
import type { Plan, Run, RunStatus } from '@/lib/runs-api';

/**
 * 待审批计划的就地卡片:放行 / 拒绝(附拒绝原因)。
 */
export function PlanApprovalCard({
  plan,
  rejectReason,
  onRejectReasonChange,
  onApprove,
  onReject,
  busy,
}: {
  plan: Plan;
  rejectReason: string;
  onRejectReasonChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  return (
    <div className="rounded-lg border border-ha-warning-border bg-ha-warning-bg/40">
      <div className="flex items-center justify-between gap-3 border-b border-ha-warning-border/60 px-3 py-2.5">
        <span className="text-[12px] font-semibold text-ha-warning">
          待放行的计划
        </span>
        <ToneBadge tone="warning">等待审批</ToneBadge>
      </div>
      <div className="max-h-64 overflow-auto px-3 py-2">
        <Markdown content={plan.plan_markdown} />
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-ha-warning-border/60 px-3 py-2.5">
        <ConsoleButton
          variant="primary"
          size="sm"
          onClick={onApprove}
          disabled={busy}
        >
          放行执行
        </ConsoleButton>
        <ConsoleButton
          variant="danger"
          size="sm"
          onClick={onReject}
          disabled={busy}
        >
          拒绝计划
        </ConsoleButton>
        <input
          aria-label="拒绝原因"
          value={rejectReason}
          onChange={(event) => onRejectReasonChange(event.target.value)}
          className="h-7 min-w-40 flex-1 rounded-md border border-ha-border-strong bg-ha-panel px-2.5 text-[12px] text-ha-text-body outline-none placeholder:text-ha-dim focus:border-ha-accent"
        />
      </div>
    </div>
  );
}

/**
 * 危险操作确认卡片(awaiting_confirmation):确认后才会执行。
 */
export function DangerConfirmCard({
  onConfirm,
  onReject,
  busy,
}: {
  onConfirm: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-ha-warning-border bg-ha-warning-bg px-3 py-3">
      <AlertTriangle className="h-4 w-4 text-ha-warning" />
      <span className="mr-auto text-[12px] text-ha-text-2">
        有危险操作需要你确认(例如对外发布),确认后才会执行。
      </span>
      <ConsoleButton
        variant="primary"
        size="sm"
        onClick={onConfirm}
        disabled={busy}
      >
        确认并执行
      </ConsoleButton>
      <ConsoleButton
        variant="danger"
        size="sm"
        onClick={onReject}
        disabled={busy}
      >
        拒绝危险操作
      </ConsoleButton>
    </div>
  );
}

/**
 * 右侧执行面板 footer 的就地动作:按 run 状态给放行/确认/查看成品/暂停。
 * 没有匹配状态时不渲染(null)。
 */
export function RunFooterAction({
  run,
  status,
  busy,
  onApprove,
  onConfirm,
  onToggleArtifact,
}: {
  run?: Run;
  status: RunStatus;
  busy: boolean;
  onApprove: (runId: string) => void;
  onConfirm: (runId: string, decision: 'approve' | 'reject') => void;
  onToggleArtifact: () => void;
}): ReactNode {
  if (status === 'awaiting_approval' && run) {
    return (
      <ConsoleButton
        variant="primary"
        size="sm"
        onClick={() => onApprove(run.id)}
        disabled={busy}
      >
        放行执行
      </ConsoleButton>
    );
  }
  if (status === 'awaiting_confirmation' && run) {
    return (
      <ConsoleButton
        variant="primary"
        size="sm"
        onClick={() => onConfirm(run.id, 'approve')}
        disabled={busy}
      >
        确认危险操作
      </ConsoleButton>
    );
  }
  if (status === 'completed') {
    return (
      <ConsoleButton variant="secondary" size="sm" onClick={onToggleArtifact}>
        查看成品
      </ConsoleButton>
    );
  }
  if (status === 'running') {
    return (
      <ConsoleButton
        variant="secondary"
        size="sm"
        disabled
        title="暂停能力暂未开放"
      >
        暂停执行
      </ConsoleButton>
    );
  }
  return null;
}
