import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import { useRunEvents } from '@/features/runs/use-run-events';
import { useChatCapabilities } from './use-chat-capabilities';
import type { Attachment, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import { modelsApi } from '@/lib/models-api';
import type { Plan, Run, RunStatus } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { skillsApi } from '@/lib/skills-api';
import type { Tone } from '@/lib/presentation';
import {
  RUN_STATUS_LABELS,
  eventLine,
  isRunTerminal,
  lastPlanEventSeq,
  lastStatusEventSeq,
  liveRunText,
  pendingWritesFrom,
  questionStateFrom,
  runStatusTone,
  stepsFrom,
  todosFrom,
  toolCallsFrom,
} from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceSelection } from '@/lib/workspace-selection';

/** 会话工作台的数据层:工作区/会话/消息/附件/run/事件流及派生展示状态;只读 + 失效,不发请求副作用。 */
export function useChatQueries(activeConversationId: string | null) {
  const queryClient = useQueryClient();
  const [, setSearchParams] = useSearchParams();
  // 会话必须归属工作区:当前工作区由 URL ?workspace 决定,缺省回落到账号默认工作区
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    isLoading: workspacesLoading,
    selectWorkspace,
  } = useWorkspaceSelection();
  // 会话必须归属于工作区:还没有工作区时主区改为引导创建,不渲染空态 hero 与输入区
  const hasNoWorkspaces = !workspacesLoading && workspaces.length === 0;

  const [manualRun, setManualRun] = useState<{
    conversationId: string;
    runId: string;
  } | null>(null);

  const conversationsQuery = useQuery({
    queryKey: [...queryKeys.conversations, activeWorkspaceId],
    queryFn: () =>
      conversationsApi.list({
        workspace_id: activeWorkspaceId ?? undefined,
      }),
    enabled: Boolean(activeWorkspaceId),
  });
  const skillsQuery = useQuery({
    queryKey: queryKeys.skills,
    queryFn: skillsApi.list,
    retry: false,
  });
  const {
    mcpServers,
    enabledMcpServers,
    mcpServerName,
    mcpCatalog,
    connectorProviders,
    connectorName,
  } = useChatCapabilities();
  // 可选模型目录(C1 §6.3):只读展示,选择器与顶栏共用同一份缓存;
  // 拉取失败不阻塞对话(退化成只有「默认模型」一项),所以不进 queryError。
  const modelsQuery = useQuery({
    queryKey: queryKeys.chatModels,
    queryFn: modelsApi.listModels,
    retry: false,
    staleTime: 60_000,
  });
  const conversations = conversationsQuery.data ?? [];

  const conversationQuery = useQuery({
    queryKey: queryKeys.conversation(activeConversationId as string),
    queryFn: () => conversationsApi.get(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });
  const conversationWorkspaceId = conversationQuery.data?.workspace_id ?? null;

  const messagesQuery = useQuery({
    queryKey: queryKeys.conversationMessages(activeConversationId as string),
    queryFn: () => conversationsApi.messages(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });
  const messages: ChatMessage[] = messagesQuery.data ?? [];

  const attachmentsQuery = useQuery({
    queryKey: queryKeys.conversationAttachments(activeConversationId as string),
    queryFn: () => conversationsApi.attachments(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });
  const attachmentsByMessage = new Map<string, Attachment[]>();
  for (const attachment of attachmentsQuery.data ?? []) {
    if (!attachment.message_id) continue;
    const list = attachmentsByMessage.get(attachment.message_id) ?? [];
    list.push(attachment);
    attachmentsByMessage.set(attachment.message_id, list);
  }

  const manualRunId =
    manualRun && manualRun.conversationId === activeConversationId
      ? manualRun.runId
      : null;
  const activeRunId =
    manualRunId ?? conversationQuery.data?.active_run_id ?? null;

  // 事件流订阅:会话工作台把步骤事件也订上(include_steps),步骤/清单随之即时到达。
  const { events, connected, reconnecting, ended } = useRunEvents(activeRunId, {
    includeSteps: true,
  });
  // 事件即投影:步骤与清单本来就是 run 事件的读模型,连上 SSE 后不必再拉端点。
  const eventSteps = stepsFrom(events);
  const eventTodos = todosFrom(events);
  const toolCalls = toolCallsFrom(events);

  const runQuery = useQuery({
    queryKey: queryKeys.run(activeRunId as string),
    queryFn: () => runsApi.get(activeRunId as string),
    enabled: Boolean(activeRunId),
    // 状态变化都有事件:SSE 在管时靠「状态类事件 → 失效」驱动,只有没连上才退回
    // 2s 轮询兜底。
    refetchInterval: (query) => {
      if (connected) return false;
      const data = query.state.data as Run | undefined;
      return data && isRunTerminal(data.status) ? false : 2000;
    },
  });

  // 计划正文在进入 awaiting_approval 时才落库(首帧是空串);它只会因规划完成/
  // 编辑/重规划而变,都有事件,所以只取一次 + 事件失效,不再 1.5s 轮询。
  const planQuery = useQuery({
    queryKey: queryKeys.plan(activeRunId as string),
    queryFn: () => runsApi.getPlan(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
  });

  // 步骤/清单读模型只在「SSE 没连上」时兜底(首帧、断线重连中):断线必须回落,
  // 否则网络抖动时界面就冻住了;连上后一个读模型请求都不发,终态也不再轮询。
  const fallbackStepsQuery = useQuery({
    queryKey: queryKeys.runSteps(activeRunId as string),
    queryFn: () => runsApi.steps(activeRunId as string),
    enabled: Boolean(activeRunId) && !connected,
    retry: false,
    refetchInterval: () =>
      !connected &&
      (runQuery.data === undefined || !isRunTerminal(runQuery.data.status))
        ? 1500
        : false,
  });
  const fallbackTodosQuery = useQuery({
    queryKey: queryKeys.runTodos(activeRunId as string),
    queryFn: () => runsApi.todos(activeRunId as string),
    enabled: Boolean(activeRunId) && !connected,
    retry: false,
    refetchInterval: () =>
      !connected &&
      (runQuery.data === undefined || !isRunTerminal(runQuery.data.status))
        ? 1500
        : false,
  });
  // 事件里有投影就以事件为准;连上但投影为空(还没产生步骤/清单)时保留兜底快照
  // ——步骤只增不减,旧快照是它的前缀。
  const steps =
    connected && eventSteps.length > 0
      ? eventSteps
      : (fallbackStepsQuery.data ?? eventSteps);
  const todos =
    connected && eventTodos.revision > 0
      ? eventTodos.todos
      : (fallbackTodosQuery.data?.todos ?? eventTodos.todos);
  const run = runQuery.data;
  const status: RunStatus = run?.status ?? 'pending';

  // 在等哪些子代理(L2 异步子代理 §2 第 26 条):只在父 run 停在 awaiting_subagent
  // 时取——子 run 有 conversation_id=None,会话里看不到它们,不主动拉就是四个字
  // 「等待子代理」。子 run 终态会把父 run 推离该状态,轮询随即停。
  const subagentsQuery = useQuery({
    queryKey: queryKeys.runSubagents(activeRunId as string),
    queryFn: () => runsApi.subagents(activeRunId as string),
    enabled: Boolean(activeRunId) && status === 'awaiting_subagent',
    retry: false,
    refetchInterval: status === 'awaiting_subagent' ? 1500 : false,
  });
  const conversation = conversationQuery.data;
  const title =
    conversation?.title ??
    conversations.find((item) => item.id === activeConversationId)?.title ??
    '新会话';
  const inFlight =
    Boolean(activeRunId) && run !== undefined && !isRunTerminal(status);
  const eventLines = events.flatMap((event) => {
    const line = eventLine(event);
    if (!line || line.tone === 'neutral') return [];
    return [{ event, line }];
  });
  // 任务执行期的实时正文:正文增量只在 run 事件流里(token.delta.v1),
  // 不能依赖 pending——任务模式收到 done 帧时 pending 就被清掉了。
  const liveRun = liveRunText(events);
  // 等审批/等工具确认/blocked 时 run 并没有在跑,实时正文要收起来交给下面的审批卡片;
  // 执行期 run 状态一直是 pending(不在终态里),所以判据是「排除等待态」而不是 running。
  const runPaused =
    status === 'awaiting_approval' ||
    status === 'awaiting_confirmation' ||
    status === 'awaiting_input' ||
    status === 'blocked';
  // ask_question 的挂起提问(§10.4):从 run 事件流推导,不新增后端读面。
  const question = questionStateFrom(events, run?.status);
  // 危险确认的待办操作明细同样只存在于事件流(run 上不落这份数据)。
  const pendingWrites = pendingWritesFrom(events);

  // 底部状态栏条目(页面外壳直接消费的视图派生)
  const statusTone = runStatusTone(status);
  const statusItems: { label: string; tone?: Tone }[] = [
    { label: `事件 ${events.length}` },
    {
      label: connected
        ? 'SSE 已连接'
        : reconnecting
          ? 'SSE 重连中'
          : 'SSE 未连接',
      tone: connected ? 'success' : reconnecting ? 'warning' : undefined,
    },
  ];
  if (activeRunId) {
    statusItems.push(
      { label: `运行 #${activeRunId.slice(0, 8)}` },
      {
        label: RUN_STATUS_LABELS[status],
        tone: statusTone === 'neutral' ? undefined : statusTone,
      },
    );
  }

  const settleConversation = async (conversationId: string) => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversationMessages(conversationId),
    });
    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversationAttachments(conversationId),
    });
    await queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
  };

  // 会话的 workspace_id 是归属的权威来源:旧链接或跨工作区打开会话时,把 URL 对齐过去。
  // 但工作区被删除后会话会落到「未分组」(workspace_id 已不在清单里):
  // 此时不能把 URL 改回那个已不存在的工作区,会话应当留在当前工作区继续用。
  useEffect(() => {
    if (
      !conversationWorkspaceId ||
      !activeWorkspaceId ||
      conversationWorkspaceId === activeWorkspaceId
    ) {
      return;
    }
    if (
      !workspaces.some((workspace) => workspace.id === conversationWorkspaceId)
    ) {
      return;
    }
    selectWorkspace(conversationWorkspaceId, { keepConversation: true });
  }, [conversationWorkspaceId, activeWorkspaceId, selectWorkspace, workspaces]);

  // 状态类事件一到就重取 run(替代 2s 轮询)。高频投影类事件(步骤/过程项/
  // 工具调用/正文增量…)不算,否则事件流会变成请求流。
  const statusSeq = lastStatusEventSeq(events);
  useEffect(() => {
    if (!activeRunId || statusSeq === 0) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.run(activeRunId),
    });
  }, [statusSeq, activeRunId, queryClient]);

  // 计划与「等待审批」是同一事务写入的:事件到了就重取计划,审批卡片不会只剩外壳。
  const planSeq = lastPlanEventSeq(events);
  useEffect(() => {
    if (!activeRunId || planSeq === 0) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.plan(activeRunId),
    });
  }, [planSeq, activeRunId, queryClient]);

  // stream.end:服务端发完最后一条事件就收尾了,这里补一次刷新(状态终值、
  // 落库的 assistant 消息、会话标题),不依赖下一条事件来触发。
  useEffect(() => {
    if (!activeRunId || !ended) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.run(activeRunId),
    });
    if (activeConversationId) {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversationMessages(activeConversationId),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
    }
  }, [ended, activeRunId, activeConversationId, queryClient]);

  // run 进终态时补一次失效:落库的 assistant 消息/会话标题要刷新出来
  const prevStatusRef = useRef<RunStatus | null>(null);
  useEffect(() => {
    const previous = prevStatusRef.current;
    prevStatusRef.current = status;
    if (
      activeConversationId &&
      previous !== null &&
      !isRunTerminal(previous) &&
      isRunTerminal(status)
    ) {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversationMessages(
          activeConversationId as string,
        ),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
    }
  }, [status, activeConversationId, queryClient]);

  // hero 里的工作区切换:hero 只在「新会话」场景出现,切完不能掉回旧会话。
  // 直接调 selectWorkspace 默认会删掉 new;而在同一次事件里连续两次 setSearchParams,
  // 后一次的函数式 updater 读到的是本次渲染的旧参数,会把前一次写入覆盖掉。
  // 所以这里只用一个 updater,一次写好 workspace 与 new。
  const changeNewSessionWorkspace = (workspaceId: string) => {
    if (!workspaceId) return;
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set('workspace', workspaceId);
        next.delete('conversation');
        next.set('new', '1');
        return next;
      },
      { replace: true },
    );
  };

  return {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    workspacesLoading,
    hasNoWorkspaces,
    selectWorkspace,
    conversations,
    skills: skillsQuery.data ?? [],
    models: modelsQuery.data?.models ?? [],
    defaultModel: modelsQuery.data?.default_model ?? null,
    mcpServers,
    enabledMcpServers,
    mcpServerName,
    mcpCatalog,
    connectorProviders,
    connectorName,
    conversation,
    messages,
    attachmentsByMessage,
    activeRunId,
    run,
    status,
    inFlight,
    events,
    connected,
    reconnecting,
    toolCalls,
    steps,
    todos,
    subagents: subagentsQuery.data ?? [],
    plan: planQuery.data,
    liveRun,
    runPaused,
    question,
    pendingWrites,
    eventLines,
    title,
    statusItems,
    manualRun,
    setManualRun,
    settleConversation,
    changeNewSessionWorkspace,
    queryError:
      runQuery.error ??
      conversationsQuery.error ??
      conversationQuery.error ??
      messagesQuery.error,
  };
}

export type ChatQueries = ReturnType<typeof useChatQueries>;

/** 审批计划的数据形状(供展示组件复用)。 */
export type ChatPlan = Plan;
