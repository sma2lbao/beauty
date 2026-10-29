/**
 * 运行详情抽屉的数据与交互模型:run / 计划 / 步骤 / 过程项 / 任务清单 / 子代理 /
 * 用量七路查询 + 事件流,以及审批、重试、取消、保存计划、排队指令等变更与
 * 取消合法性等派生状态。抽屉组件只负责布局,不再自己拼查询。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { useRunEvents } from '@/features/runs/use-run-events';
import { isRunQuiescent } from '@/lib/presentation';
import type { Run, RunItem, RunStatus, RunStep } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { queryKeys } from '@/lib/query-keys';

/** 可发起取消的状态:与后端 run_lifecycle.TRANSITIONS["cancel"] 对齐。 */
const CANCELLABLE: readonly RunStatus[] = [
  'pending',
  'planning',
  'running',
  'awaiting_approval',
  'awaiting_confirmation',
  'awaiting_input',
  'blocked',
];

export function useRunDetail({
  runId,
  onSelectRun,
}: {
  runId: string;
  onSelectRun?: (nextRunId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [planDraft, setPlanDraft] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('先别发,我要改一下');
  const [instruction, setInstruction] = useState('');
  const [showArtifact, setShowArtifact] = useState(false);

  const runQuery = useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => runsApi.get(runId),
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && isRunQuiescent(data.status) ? false : 2000;
    },
  });
  const planQuery = useQuery({
    queryKey: queryKeys.plan(runId),
    queryFn: () => runsApi.getPlan(runId),
  });
  const instructionsQuery = useQuery({
    queryKey: queryKeys.instructions(runId),
    queryFn: () => runsApi.instructions(runId),
  });
  // 步骤投影不进 SSE:执行中按 1.5s 轮询,静默终态(含 blocked)停。
  const stepsQuery = useQuery({
    queryKey: queryKeys.runSteps(runId),
    queryFn: () => runsApi.steps(runId),
    refetchInterval: (query) => {
      const data = query.state.data as RunStep[] | undefined;
      if (data === undefined) return 1500;
      return runQuery.data && isRunQuiescent(runQuery.data.status)
        ? false
        : 1500;
    },
  });
  // 统一过程项(计划/工具/文件变更/MCP/检索/清单)是 P1-D 新投影:
  // 与步骤同频轮询,静默终态停;空数组是合法返回值(没有 item 事件)。
  const itemsQuery = useQuery({
    queryKey: queryKeys.runItems(runId),
    queryFn: () => runsApi.items(runId),
    refetchInterval: (query) => {
      const data = query.state.data as RunItem[] | undefined;
      if (data === undefined) return 1500;
      return runQuery.data && isRunQuiescent(runQuery.data.status)
        ? false
        : 1500;
    },
  });
  // 任务清单同样不进 SSE:执行中与步骤同频轮询,静默终态停。
  const todosQuery = useQuery({
    queryKey: queryKeys.runTodos(runId),
    queryFn: () => runsApi.todos(runId),
    refetchInterval: () =>
      runQuery.data && isRunQuiescent(runQuery.data.status) ? false : 1500,
  });
  // 子代理列表与 run 用量(P1-E):父 run 执行中 1.5s 轮询,静默终态停。
  // 子代理是独立 run,这里只取摘要;详情经 onSelectRun 切到子 run。
  const subagentsQuery = useQuery({
    queryKey: queryKeys.runSubagents(runId),
    queryFn: () => runsApi.subagents(runId),
    refetchInterval: () =>
      runQuery.data && isRunQuiescent(runQuery.data.status) ? false : 1500,
  });
  const usageQuery = useQuery({
    queryKey: queryKeys.runUsage(runId),
    queryFn: () => runsApi.usage(runId),
    refetchInterval: () =>
      runQuery.data && isRunQuiescent(runQuery.data.status) ? false : 1500,
  });
  const { events, connected } = useRunEvents(runId);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.plan(runId) }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.instructions(runId),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runSteps(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runItems(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runTodos(runId) }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.runSubagents(runId),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runUsage(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
    ]);
  };

  const retryMutation = useMutation({
    mutationFn: () => runsApi.retry(runId),
    onSuccess: async (created) => {
      await refresh();
      onSelectRun?.(created.id);
    },
  });
  const action = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: refresh,
  });
  const savePlan = useMutation({
    mutationFn: () => runsApi.updatePlan(runId, planDraft ?? ''),
    onSuccess: refresh,
  });
  const cancelMutation = useMutation({
    mutationFn: () => runsApi.cancel(runId),
    onSuccess: refresh,
  });
  const queueInstruction = useMutation({
    mutationFn: () => runsApi.queueInstruction(runId, instruction.trim()),
    onSuccess: async () => {
      setInstruction('');
      await refresh();
    },
  });

  const run = runQuery.data;
  const plan = planQuery.data;
  const subagents = subagentsQuery.data ?? [];
  // 有子代理 run、或父级已出现 subagent 过程项时才显示面板(§10)。
  const showSubagents =
    subagents.length > 0 ||
    (itemsQuery.data ?? []).some((item) => item.kind === 'subagent');
  // 子代理合计(§9):父 run 自身用量来自 /usage,子代理合计由各行相加。
  const subagentCalls = subagents.reduce(
    (total, subagent) => total + subagent.usage.calls,
    0,
  );
  const subagentTokens = subagents.reduce(
    (total, subagent) => total + subagent.usage.total_tokens,
    0,
  );
  const status = run?.status ?? 'pending';
  const editable = status === 'awaiting_approval';
  const canCancel = run !== undefined && CANCELLABLE.includes(status);
  const reasonLabel =
    status === 'failed'
      ? '失败原因'
      : status === 'cancelled'
        ? '取消原因'
        : '中断原因';

  return {
    runId,
    runQuery,
    planQuery,
    instructionsQuery,
    stepsQuery,
    itemsQuery,
    todosQuery,
    subagentsQuery,
    usageQuery,
    events,
    connected,
    retryMutation,
    action,
    savePlan,
    cancelMutation,
    queueInstruction,
    planDraft,
    setPlanDraft,
    rejectReason,
    setRejectReason,
    instruction,
    setInstruction,
    showArtifact,
    setShowArtifact,
    run,
    plan,
    subagents,
    showSubagents,
    subagentCalls,
    subagentTokens,
    status,
    editable,
    canCancel,
    reasonLabel,
  };
}

export type RunDetailModel = ReturnType<typeof useRunDetail>;
