import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { ApprovalGrant } from '@/lib/approval-api';
import { conversationsApi } from '@/lib/conversations-api';
import type { RunAnswerPayload } from '@/lib/runs-api';
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
  const confirmDanger = (
    runId: string,
    decision: 'approve' | 'reject',
    grant?: ApprovalGrant,
  ) => action.mutate(() => runsApi.confirm(runId, decision, undefined, grant));
  /** 回答/跳过挂起提问(ask_question):成功后失效 run 与会话列表。 */
  const answerQuestion = async (
    runId: string,
    body: RunAnswerPayload,
  ): Promise<void> => {
    await action.mutateAsync(() => runsApi.answerRunQuestion(runId, body));
  };

  /**
   * 切换已有会话的默认模型(§6.3):PATCH `ConversationUpdate.model`,
   * null 表示回到「默认模型」。成功后会话详情与列表都失效,头部/选择器随之更新;
   * 白名单拒绝(400 `llm.model_not_allowed`)由 error 走页面错误条展示。
   */
  const modelMutation = useMutation({
    mutationFn: (input: { conversationId: string; model: string | null }) =>
      conversationsApi.update(input.conversationId, { model: input.model }),
    onSuccess: async (_data, input) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversation(input.conversationId),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
      ]);
    },
  });
  const updateModel = (conversationId: string, model: string | null) =>
    modelMutation.mutate({ conversationId, model });

  /**
   * 审批前编辑计划(interaction-closure §3):PATCH 已存在的 /plan 端点,
   * 成功后失效 plan/run/会话列表;失败由审批卡就地展示(不弹全局错误条)。
   */
  const planMutation = useMutation({
    mutationFn: (input: { runId: string; planMarkdown: string }) =>
      runsApi.updatePlan(input.runId, input.planMarkdown),
    onSuccess: async (_plan, input) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.plan(input.runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.run(input.runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
      ]);
    },
  });
  const savePlan = async (
    runId: string,
    planMarkdown: string,
  ): Promise<void> => {
    await planMutation.mutateAsync({ runId, planMarkdown });
  };

  return {
    busy: action.isPending,
    error: action.error ?? modelMutation.error,
    rejectReason,
    setRejectReason,
    approve,
    rejectPlan,
    confirmDanger,
    answerQuestion,
    updateModel,
    savePlan,
    planBusy: planMutation.isPending,
    planError: planMutation.error,
  };
}

export type ChatActions = ReturnType<typeof useChatActions>;
