import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { runsApi } from '@/lib/runs-api';
import { queryKeys } from '@/lib/query-keys';

/**
 * 审批/确认动作层:统一走一个 mutation,成功后刷新 run 与会话列表。
 * 失败信息由调用方从 error 读取并展示。
 */
export function useChatActions(activeRunId: string | null) {
  const queryClient = useQueryClient();
  const [rejectReason, setRejectReason] = useState('先别发,我要改一下');

  const action = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.run(activeRunId as string),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
      ]);
    },
  });

  const approve = (runId: string) =>
    action.mutate(() => runsApi.approve(runId));
  const rejectPlan = (runId: string) =>
    action.mutate(() => runsApi.reject(runId, rejectReason));
  const confirmDanger = (runId: string, decision: 'approve' | 'reject') =>
    action.mutate(() => runsApi.confirm(runId, decision));

  return {
    busy: action.isPending,
    error: action.error,
    rejectReason,
    setRejectReason,
    approve,
    rejectPlan,
    confirmDanger,
  };
}

export type ChatActions = ReturnType<typeof useChatActions>;
