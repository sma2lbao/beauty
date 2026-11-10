import { useState, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

import { RUN_GRANT_LABELS } from '@/components/console/grant-labels';
import { GrantPicker } from '@/components/console/grant-picker';
import { Markdown } from '@/components/console/markdown';
import { ConsoleButton, ToneBadge } from '@/components/console/ui';
import type { ApprovalGrant } from '@/lib/approval-api';
import { summarizeArguments, type PendingWrite } from '@/lib/presentation';
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
        {plan.plan_markdown.trim() ? (
          <Markdown content={plan.plan_markdown} />
        ) : (
          // 计划正文是异步落库的:planning 阶段取到的空串会被 react-query 先缓存,
          // 这里给明确占位并提示会自动刷新,避免卡片只剩外壳。
          <p className="text-[12px] leading-5 text-ha-dim">
            计划正文暂未生成,稍后会自动刷新;也可以直接放行或拒绝。
          </p>
        )}
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
 *
 * 待确认的操作清单来自 `run.confirmation.required.v1` 事件载荷的
 * `pending_writes`(run 上不落这份明细),没有明细时给兜底说明,
 * 不再只渲染一句通用提示让用户猜「到底要确认什么」。
 */
export function DangerConfirmCard({
  writes,
  onConfirm,
  onReject,
  busy,
}: {
  writes: PendingWrite[];
  /** 授权范围(P1-A §7.2):由卡片内部三段控件选择,默认本次。 */
  onConfirm: (grant: ApprovalGrant) => void;
  onReject: () => void;
  busy: boolean;
}) {
  const [grant, setGrant] = useState<ApprovalGrant>('once');
  return (
    <div className="rounded-lg border border-ha-warning-border bg-ha-warning-bg/40">
      <div className="flex items-center justify-between gap-3 border-b border-ha-warning-border/60 px-3 py-2.5">
        <span className="flex items-center gap-2 text-[12px] font-semibold text-ha-warning">
          <AlertTriangle className="h-4 w-4" />
          {writes.length > 0
            ? `有 ${writes.length} 个危险操作待确认`
            : '有危险操作待确认'}
        </span>
        <ToneBadge tone="warning">等待确认</ToneBadge>
      </div>
      <div className="space-y-2 px-3 py-2.5">
        <p className="text-[12px] leading-5 text-ha-text-2">
          有危险操作需要你确认(例如对外发布),确认后才会执行。
        </p>
        {writes.length > 0 ? (
          <ol className="space-y-1.5">
            {writes.map((write) => (
              <li
                key={write.id}
                className="rounded-md border border-ha-border bg-ha-panel px-2.5 py-1.5"
              >
                <p className="break-all font-mono text-[12px] text-ha-text-body">
                  {write.name}
                </p>
                <p className="break-words font-mono text-[11px] leading-4 text-ha-dim">
                  {summarizeArguments(write.arguments) || '无参数'}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-[12px] leading-5 text-ha-dim">
            {
              '待确认的操作明细尚未同步(旧版本 run 可能没有这份明细),请结合上文执行计划判断后再确认。'
            }
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-ha-warning-border/60 px-3 py-2.5">
        <GrantPicker
          value={grant}
          onChange={setGrant}
          disabled={busy}
          labels={RUN_GRANT_LABELS}
        />
        <ConsoleButton
          variant="primary"
          size="sm"
          onClick={() => onConfirm(grant)}
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
