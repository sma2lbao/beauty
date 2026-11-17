/**
 * 检查点页签的数据层(P1-C):检查点列表 + 回退/分叉两次变更 + 回退前的副作用台账(S5)。
 *
 * 回退是「同线程重跑」:成功后 run 仍在原 id 上,失效 run/步骤/运行项/待办
 * 与检查点列表即可;分叉会返回一个新 run,由调用方把详情切到新 run。
 *
 * S5:回退/分叉会把检查点之后的节点按未执行重跑,已发生的外部写不会回滚——
 * 同参数调用按稳定幂等键重放,参数变了就是一次新的外部写。所以弹窗打开时先拉
 * 台账,有写时必须勾选知悉后才能提交;台账没取到(或提交期又冒出新写)时由后端
 * 409 `run.side_effects_present` 兜底,错误 details 里带同一份明细。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import { runsApi } from '@/lib/runs-api';
import type { Run, RunCheckpoint, RunSideEffect } from '@/lib/runs-api';

/** 允许回退的 run 状态:与后端 TRANSITIONS.rewind 一致,运行中/待审批需先 cancel。 */
export const REWINDABLE_RUN_STATUSES: Run['status'][] = [
  'completed',
  'failed',
  'cancelled',
  'blocked',
];

/** 把检查点操作的错误翻成中文:409 checkpoint 失效/副作用未确认是最常见的可恢复错误。 */
export function checkpointErrorMessage(
  error: unknown,
  fallback = '操作失败,请重试。',
): string {
  if (error instanceof ApiError) {
    if (error.code === 'run.checkpoint_not_found') {
      return '检查点不存在或不属于当前 run(可能已被清理),请刷新列表后重试。';
    }
    if (error.code === 'run.side_effects_present') {
      return '该 run 已发起过外部写操作,回退/分叉后重跑可能重复执行;请勾选「我已知悉」后再试。';
    }
    return error.message || fallback;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * 从 409 `run.side_effects_present` 的 details 还原台账。
 *
 * 后端 details 里的明细是精简形态(不含参数全文与授权方式),这里补默认值,
 * 让台账没拉到(例如打开弹窗后又有新的写)时弹窗仍能列出「到底写过什么」。
 */
export function sideEffectsFromError(error: unknown): RunSideEffect[] {
  if (!(error instanceof ApiError) || error.code !== 'run.side_effects_present') {
    return [];
  }
  const items = error.details?.side_effects;
  if (!Array.isArray(items)) {
    return [];
  }
  return items.flatMap((item) => {
    if (!item || typeof item !== 'object') {
      return [];
    }
    const record = item as Record<string, unknown>;
    if (typeof record.name !== 'string' || record.name.length === 0) {
      return [];
    }
    return [
      {
        seq: typeof record.seq === 'number' ? record.seq : 0,
        tool_call_id:
          typeof record.tool_call_id === 'string' ? record.tool_call_id : null,
        name: record.name,
        risk: typeof record.risk === 'string' ? record.risk : 'write',
        decision: typeof record.decision === 'string' ? record.decision : 'auto',
        ok: record.ok === true,
        arguments:
          typeof record.arguments === 'string' ? record.arguments : '',
      },
    ];
  });
}

export function useRunCheckpoints({
  runId,
  onSelectRun,
}: {
  runId: string;
  onSelectRun?: (nextRunId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [rewindTarget, setRewindTarget] = useState<RunCheckpoint | null>(null);
  const [reason, setReason] = useState('');
  const [forkTarget, setForkTarget] = useState<RunCheckpoint | null>(null);
  const [forkTitle, setForkTitle] = useState('');
  /** S5:有外部写时必须显式勾选,换弹窗/提交成功都重置。 */
  const [acknowledgeSideEffects, setAcknowledgeSideEffects] = useState(false);

  const checkpointsQuery = useQuery({
    queryKey: queryKeys.runCheckpoints(runId),
    queryFn: () => runsApi.checkpoints(runId),
  });

  const dialogOpen = rewindTarget !== null || forkTarget !== null;
  const sideEffectsQuery = useQuery({
    queryKey: queryKeys.runSideEffects(runId),
    queryFn: () => runsApi.sideEffects(runId),
    // 只在回退/分叉弹窗打开时取:没人要回退就不打扰后端
    enabled: dialogOpen,
  });

  const rewindMutation = useMutation({
    mutationFn: (input: {
      checkpointId: string;
      reason: string;
      acknowledgeSideEffects: boolean;
    }) =>
      runsApi.rewind(runId, {
        checkpoint_id: input.checkpointId,
        ...(input.reason.trim() ? { reason: input.reason.trim() } : {}),
        ...(input.acknowledgeSideEffects
          ? { acknowledge_side_effects: true }
          : {}),
      }),
    onSuccess: async () => {
      setRewindTarget(null);
      setReason('');
      setAcknowledgeSideEffects(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runCheckpoints(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runSteps(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runItems(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runTodos(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runSideEffects(runId) }),
      ]);
    },
  });

  const forkMutation = useMutation({
    mutationFn: (input: {
      checkpointId: string;
      title: string;
      acknowledgeSideEffects: boolean;
    }) =>
      runsApi.fork(runId, {
        checkpoint_id: input.checkpointId,
        ...(input.title.trim() ? { title: input.title.trim() } : {}),
        ...(input.acknowledgeSideEffects
          ? { acknowledge_side_effects: true }
          : {}),
      }),
    onSuccess: async (created) => {
      setForkTarget(null);
      setForkTitle('');
      setAcknowledgeSideEffects(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
      onSelectRun?.(created.id);
    },
  });

  // 台账优先用接口结果;接口空但提交吃到 409(弹窗打开后又写了)时用错误里的明细。
  const fetchedEffects = sideEffectsQuery.data ?? [];
  const sideEffects =
    fetchedEffects.length > 0
      ? fetchedEffects
      : sideEffectsFromError(rewindMutation.error ?? forkMutation.error);
  const blockedBySideEffects = sideEffects.length > 0 && !acknowledgeSideEffects;

  return {
    checkpoints: checkpointsQuery.data ?? [],
    isLoading: checkpointsQuery.isPending,
    error: checkpointsQuery.error,
    refetch: () => void checkpointsQuery.refetch(),

    /** 回退/分叉前的副作用台账(S5)。 */
    sideEffects,
    sideEffectsLoading: dialogOpen && sideEffectsQuery.isPending,
    sideEffectsError: sideEffectsQuery.error,
    acknowledgeSideEffects,
    setAcknowledgeSideEffects,

    rewindTarget,
    openRewind: (checkpoint: RunCheckpoint) => {
      rewindMutation.reset();
      setRewindTarget(checkpoint);
      setReason('');
      setAcknowledgeSideEffects(false);
    },
    closeRewind: () => setRewindTarget(null),
    reason,
    setReason,
    confirmRewind: () => {
      if (!rewindTarget) return;
      rewindMutation.mutate({
        checkpointId: rewindTarget.checkpoint_id,
        reason,
        acknowledgeSideEffects,
      });
    },
    rewindPending: rewindMutation.isPending,
    rewindError: rewindMutation.error,
    /** 有写又没勾选知悉:确认按钮禁用(勾选后即可提交)。 */
    rewindBlocked: blockedBySideEffects,

    forkTarget,
    openFork: (checkpoint: RunCheckpoint) => {
      forkMutation.reset();
      setForkTarget(checkpoint);
      setForkTitle('');
      setAcknowledgeSideEffects(false);
    },
    closeFork: () => setForkTarget(null),
    forkTitle,
    setForkTitle,
    confirmFork: () => {
      if (!forkTarget) return;
      forkMutation.mutate({
        checkpointId: forkTarget.checkpoint_id,
        title: forkTitle,
        acknowledgeSideEffects,
      });
    },
    forkPending: forkMutation.isPending,
    forkError: forkMutation.error,
    forkBlocked: blockedBySideEffects,
  };
}

export type RunCheckpointsModel = ReturnType<typeof useRunCheckpoints>;
