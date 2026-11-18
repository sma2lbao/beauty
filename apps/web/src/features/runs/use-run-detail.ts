/**
 * 运行详情抽屉的数据与交互模型:run / 计划 / 步骤 / 过程项 / 任务清单 / 子代理 /
 * 用量七路读取 + 事件流,以及审批、重试、取消、保存计划、排队指令等变更与
 * 取消合法性等派生状态。抽屉组件只负责布局,不再自己拼查询。
 *
 * 读模型口径与会话工作台一致(事件分类学 §11/§13):步骤/过程项/清单都是 run
 * 事件的纯投影,SSE 连上时直接由事件算出,断线才回落端点;run 状态、计划、
 * 用量、子代理清单各自只在自己的事件到达时失效重取,不再定时轮询。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { useRunEvents } from '@/features/runs/use-run-events';
import {
  isRunTerminal,
  itemsFrom,
  lastPlanEventSeq,
  lastStatusEventSeq,
  lastSubagentEventSeq,
  lastUsageEventSeq,
  stepsFrom,
  todosFrom,
} from '@/lib/presentation';
import type { Run, RunStatus } from '@/lib/runs-api';
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
  'awaiting_subagent',
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

  // 抽屉的事件面板本来就订阅这条流;顺带打开 include_steps,步骤事件也就到了,
  // 三个读模型(步骤/过程项/清单)随事件即时算出。
  const { events, connected, ended } = useRunEvents(runId, {
    includeSteps: true,
  });
  const eventSteps = stepsFrom(events);
  const eventTodos = todosFrom(events);
  const eventItems = itemsFrom(events);

  // 状态变化都有事件:连上时靠「状态类事件 → 失效」驱动,只有没连上才退回
  // 2s 轮询兜底(断线必须回落,否则连不上时界面是死的)。
  const runQuery = useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => runsApi.get(runId),
    refetchInterval: (query) => {
      if (connected) return false;
      const data = query.state.data as Run | undefined;
      return data && isRunTerminal(data.status) ? false : 2000;
    },
  });
  const run = runQuery.data;
  const status: RunStatus = run?.status ?? 'pending';
  // 计划只在规划完成/编辑时变,打开取一次 + 事件失效,不轮询。
  const planQuery = useQuery({
    queryKey: queryKeys.plan(runId),
    queryFn: () => runsApi.getPlan(runId),
  });
  const instructionsQuery = useQuery({
    queryKey: queryKeys.instructions(runId),
    queryFn: () => runsApi.instructions(runId),
  });
  // 步骤/过程项/清单的端点只做「SSE 没连上」时的兜底(首帧、断线重连中、流已收尾):
  // 连上后一个读模型请求都不发,终态也不再轮询。
  const fallbackStepsQuery = useQuery({
    queryKey: queryKeys.runSteps(runId),
    queryFn: () => runsApi.steps(runId),
    enabled: !connected && !ended,
    retry: false,
    refetchInterval: () =>
      !connected && !ended && !isRunTerminal(status) ? 1500 : false,
  });
  const fallbackItemsQuery = useQuery({
    queryKey: queryKeys.runItems(runId),
    queryFn: () => runsApi.items(runId),
    enabled: !connected && !ended,
    retry: false,
    refetchInterval: () =>
      !connected && !ended && !isRunTerminal(status) ? 1500 : false,
  });
  const fallbackTodosQuery = useQuery({
    queryKey: queryKeys.runTodos(runId),
    queryFn: () => runsApi.todos(runId),
    enabled: !connected && !ended,
    retry: false,
    refetchInterval: () =>
      !connected && !ended && !isRunTerminal(status) ? 1500 : false,
  });
  // 事件里有投影就以事件为准;连上但投影为空(还没产生)时保留兜底快照
  // ——步骤/过程项只增不减,旧快照是它的前缀。
  const steps =
    connected && eventSteps.length > 0
      ? eventSteps
      : (fallbackStepsQuery.data ?? eventSteps);
  const items =
    connected && eventItems.length > 0
      ? eventItems
      : (fallbackItemsQuery.data ?? eventItems);
  const todos =
    connected && eventTodos.revision > 0
      ? eventTodos.todos
      : (fallbackTodosQuery.data?.todos ?? eventTodos.todos);
  // 子代理是独立 run,这里只取摘要(详情经 onSelectRun 切到子 run):
  // 等子代理时(且没连上)才 1.5s 兜底——子 run 终态会把父 run 推离该状态。
  const subagentsQuery = useQuery({
    queryKey: queryKeys.runSubagents(runId),
    queryFn: () => runsApi.subagents(runId),
    refetchInterval: () =>
      !connected && status === 'awaiting_subagent' ? 1500 : false,
  });
  // 用量只在模型调用边界变化(run.usage.v1),靠事件失效,不轮询。
  const usageQuery = useQuery({
    queryKey: queryKeys.runUsage(runId),
    queryFn: () => runsApi.usage(runId),
  });
  // 评分(P2):人工点赞/点踩写事件 + best-effort 推送 Langfuse,
  // 读侧不轮询,提交成功后只失效自己的缓存。
  const scoresQuery = useQuery({
    queryKey: queryKeys.runScores(runId),
    queryFn: () => runsApi.scores(runId),
  });

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

  // 事件驱动的失效:各读模型只认自己的事件,避免每帧都重拉。
  // 状态类事件过滤了步骤/过程项/工具调用/正文增量这类高频投影(投影已由事件算出)。
  const statusSeq = lastStatusEventSeq(events);
  const planSeq = lastPlanEventSeq(events);
  const usageSeq = lastUsageEventSeq(events);
  const subagentSeq = lastSubagentEventSeq(events);
  useEffect(() => {
    if (statusSeq === 0) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
  }, [statusSeq, runId, queryClient]);
  useEffect(() => {
    if (planSeq === 0) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.plan(runId) });
  }, [planSeq, runId, queryClient]);
  useEffect(() => {
    if (usageSeq === 0) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.runUsage(runId) });
  }, [usageSeq, runId, queryClient]);
  useEffect(() => {
    if (subagentSeq === 0) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.runSubagents(runId),
    });
  }, [subagentSeq, runId, queryClient]);
  // 流正常收尾(stream.end)补一次收尾刷新:终值状态、落库的计划与用量。
  useEffect(() => {
    if (!ended) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.plan(runId) });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.instructions(runId),
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.runSteps(runId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.runItems(runId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.runTodos(runId) });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.runSubagents(runId),
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.runUsage(runId) });
  }, [ended, runId, queryClient]);

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
  const scoreMutation = useMutation({
    mutationFn: (value: number) =>
      runsApi.createScore(runId, { name: 'user-feedback', value }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.runScores(runId) }),
  });

  const plan = planQuery.data;
  const subagents = subagentsQuery.data ?? [];
  // 有子代理 run、或父级已出现 subagent 过程项时才显示面板(§10)。
  const showSubagents =
    subagents.length > 0 || items.some((item) => item.kind === 'subagent');
  // 子代理合计(§9):父 run 自身用量来自 /usage,子代理合计由各行相加。
  const subagentCalls = subagents.reduce(
    (total, subagent) => total + subagent.usage.calls,
    0,
  );
  const subagentTokens = subagents.reduce(
    (total, subagent) => total + subagent.usage.total_tokens,
    0,
  );
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
    steps,
    items,
    todos,
    subagentsQuery,
    usageQuery,
    scoresQuery,
    events,
    connected,
    retryMutation,
    action,
    savePlan,
    cancelMutation,
    queueInstruction,
    scoreMutation,
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