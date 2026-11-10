/**
 * 检查点页签的数据层(P1-C):检查点列表 + 回退/分叉两次变更。
 *
 * 回退是「同线程重跑」:成功后 run 仍在原 id 上,失效 run/步骤/运行项/待办
 * 与检查点列表即可;分叉会返回一个新 run,由调用方把详情切到新 run。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import { runsApi } from '@/lib/runs-api';
import type { Run, RunCheckpoint } from '@/lib/runs-api';

/** 允许回退的 run 状态:与后端 TRANSITIONS.rewind 一致,运行中/待审批需先 cancel。 */
export const REWINDABLE_RUN_STATUSES: Run['status'][] = [
  'completed',
  'failed',
  'cancelled',
  'blocked',
];

/** 把检查点操作的错误翻成中文:409 checkpoint 失效是最常见的可恢复错误。 */
export function checkpointErrorMessage(
  error: unknown,
  fallback = '操作失败,请重试。',
): string {
  if (error instanceof ApiError) {
    if (error.code === 'run.checkpoint_not_found') {
      return '检查点不存在或不属于当前 run(可能已被清理),请刷新列表后重试。';
    }
    return error.message || fallback;
  }
  return error instanceof Error && error.message ? error.message : fallback;
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

  const checkpointsQuery = useQuery({
    queryKey: queryKeys.runCheckpoints(runId),
    queryFn: () => runsApi.checkpoints(runId),
  });

  const rewindMutation = useMutation({
    mutationFn: (input: { checkpointId: string; reason: string }) =>
      runsApi.rewind(runId, {
        checkpoint_id: input.checkpointId,
        ...(input.reason.trim() ? { reason: input.reason.trim() } : {}),
      }),
    onSuccess: async () => {
      setRewindTarget(null);
      setReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runCheckpoints(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runSteps(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runItems(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runTodos(runId) }),
      ]);
    },
  });

  const forkMutation = useMutation({
    mutationFn: (input: { checkpointId: string; title: string }) =>
      runsApi.fork(runId, {
        checkpoint_id: input.checkpointId,
        ...(input.title.trim() ? { title: input.title.trim() } : {}),
      }),
    onSuccess: async (created) => {
      setForkTarget(null);
      setForkTitle('');
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
      onSelectRun?.(created.id);
    },
  });

  return {
    checkpoints: checkpointsQuery.data ?? [],
    isLoading: checkpointsQuery.isPending,
    error: checkpointsQuery.error,
    refetch: () => void checkpointsQuery.refetch(),

    rewindTarget,
    openRewind: (checkpoint: RunCheckpoint) => {
      rewindMutation.reset();
      setRewindTarget(checkpoint);
      setReason('');
    },
    closeRewind: () => setRewindTarget(null),
    reason,
    setReason,
    confirmRewind: () => {
      if (!rewindTarget) return;
      rewindMutation.mutate({
        checkpointId: rewindTarget.checkpoint_id,
        reason,
      });
    },
    rewindPending: rewindMutation.isPending,
    rewindError: rewindMutation.error,

    forkTarget,
    openFork: (checkpoint: RunCheckpoint) => {
      forkMutation.reset();
      setForkTarget(checkpoint);
      setForkTitle('');
    },
    closeFork: () => setForkTarget(null),
    forkTitle,
    setForkTitle,
    confirmFork: () => {
      if (!forkTarget) return;
      forkMutation.mutate({
        checkpointId: forkTarget.checkpoint_id,
        title: forkTitle,
      });
    },
    forkPending: forkMutation.isPending,
    forkError: forkMutation.error,
  };
}

export type RunCheckpointsModel = ReturnType<typeof useRunCheckpoints>;
