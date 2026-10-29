/**
 * 运行详情操作区:按 run 状态给出生成计划/放行/拒绝/确认/重试/取消/查看交付物,
 * 并显示各变更的错误(失败不静默)。
 */
import { ConsoleButton } from '@/components/console/ui';
import { runsApi } from '@/lib/runs-api';

import type { RunDetailModel } from './use-run-detail';

export function RunDetailActions({ model }: { model: RunDetailModel }) {
  const {
    action,
    canCancel,
    cancelMutation,
    editable,
    rejectReason,
    retryMutation,
    runId,
    setRejectReason,
    setShowArtifact,
    status,
  } = model;
  return (
    <>
      <div className="mt-3 flex flex-wrap gap-2">
        {status === 'pending' ? (
          <ConsoleButton
            variant="primary"
            size="sm"
            onClick={() => action.mutate(() => runsApi.generatePlan(runId))}
            disabled={action.isPending}
          >
            生成计划
          </ConsoleButton>
        ) : null}

        {editable ? (
          <>
            <ConsoleButton
              variant="primary"
              size="sm"
              onClick={() => action.mutate(() => runsApi.approve(runId))}
              disabled={action.isPending}
            >
              放行执行
            </ConsoleButton>
            <ConsoleButton
              variant="danger"
              size="sm"
              onClick={() =>
                action.mutate(() => runsApi.reject(runId, rejectReason))
              }
              disabled={action.isPending}
            >
              拒绝计划
            </ConsoleButton>
            <input
              aria-label="拒绝原因"
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
              className="h-7 min-w-40 flex-1 rounded-md border border-ha-border-strong bg-ha-panel-2 px-2.5 text-[12px] text-ha-text-body outline-none focus:border-ha-accent"
            />
          </>
        ) : null}

        {status === 'blocked' ? (
          <ConsoleButton
            variant="primary"
            size="sm"
            onClick={() => action.mutate(() => runsApi.resume(runId))}
            disabled={action.isPending}
          >
            重新规划并进闸
          </ConsoleButton>
        ) : null}

        {status === 'awaiting_confirmation' ? (
          <>
            <ConsoleButton
              variant="primary"
              size="sm"
              onClick={() =>
                action.mutate(() => runsApi.confirm(runId, 'approve'))
              }
              disabled={action.isPending}
            >
              确认执行危险操作
            </ConsoleButton>
            <ConsoleButton
              variant="danger"
              size="sm"
              onClick={() =>
                action.mutate(() => runsApi.confirm(runId, 'reject'))
              }
              disabled={action.isPending}
            >
              拒绝危险操作
            </ConsoleButton>
          </>
        ) : null}

        {status === 'failed' ? (
          <ConsoleButton
            variant="primary"
            size="sm"
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending}
          >
            {retryMutation.isPending ? '重试中…' : '重试(另起一跑)'}
          </ConsoleButton>
        ) : null}

        {status === 'completed' ? (
          <ConsoleButton
            variant="secondary"
            size="sm"
            onClick={() => setShowArtifact(true)}
          >
            查看交付物
          </ConsoleButton>
        ) : null}

        {canCancel ? (
          <ConsoleButton
            variant="danger"
            size="sm"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
          >
            {cancelMutation.isPending ? '取消中…' : '取消任务'}
          </ConsoleButton>
        ) : null}
      </div>

      {action.isError ? (
        <p className="mt-2 text-[12px] text-ha-danger">
          {action.error.message}
        </p>
      ) : null}
      {retryMutation.isError ? (
        <p className="mt-2 text-[12px] text-ha-danger">
          {retryMutation.error.message}
        </p>
      ) : null}
      {cancelMutation.isError ? (
        <p className="mt-2 text-[12px] text-ha-danger">
          {cancelMutation.error.message}
        </p>
      ) : null}
    </>
  );
}
