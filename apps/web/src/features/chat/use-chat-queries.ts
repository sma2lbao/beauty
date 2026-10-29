import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import { useRunEvents } from '@/features/runs/use-run-events';
import type { Attachment, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import { mcpApi } from '@/lib/mcp-api';
import { modelsApi } from '@/lib/models-api';
import type { Plan, Run, RunStatus } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { skillsApi } from '@/lib/skills-api';
import type { Tone } from '@/lib/presentation';
import {
  RUN_STATUS_LABELS,
  eventLine,
  isRunTerminal,
  liveRunText,
  questionStateFrom,
  runStatusTone,
  toolCallsFrom,
} from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceSelection } from '@/lib/workspace-selection';

/**
 * 会话工作台的数据层:工作区/会话/消息/附件/run/事件流,以及由它们派生的
 * 展示状态。只读 + 失效,不发请求副作用。
 */
export function useChatQueries(activeConversationId: string | null) {
  const queryClient = useQueryClient();
  const [, setSearchParams] = useSearchParams();
  // 会话必须归属工作区:当前工作区由 URL ?workspace 决定,缺省回落到租户默认工作区
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
  const mcpQuery = useQuery({
    queryKey: queryKeys.mcpServers,
    queryFn: mcpApi.list,
    retry: false,
  });
  // 可选模型目录(C1 §6.3):只读展示,选择器与顶栏共用同一份缓存;
  // 拉取失败不阻塞对话(退化成只有「默认模型」一项),所以不进 queryError。
  const modelsQuery = useQuery({
    queryKey: queryKeys.chatModels,
    queryFn: modelsApi.listModels,
    retry: false,
    staleTime: 60_000,
  });
  const conversations = conversationsQuery.data ?? [];
  // 选择器只列启用的 server;历史消息/URL 里可能引用已停用的 id,用全量列表展示名字
  const mcpServers = mcpQuery.data ?? [];
  const enabledMcpServers = mcpServers.filter((server) => server.enabled);
  const mcpServerName = (id: string) =>
    mcpServers.find((server) => server.id === id)?.name ?? id;
  const mcpCatalog = mcpServers.map((server) => ({
    id: server.id,
    name: server.name,
  }));

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

  const runQuery = useQuery({
    queryKey: queryKeys.run(activeRunId as string),
    queryFn: () => runsApi.get(activeRunId as string),
    enabled: Boolean(activeRunId),
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && isRunTerminal(data.status) ? false : 2000;
    },
  });
  const { events, connected, reconnecting } = useRunEvents(activeRunId);
  const toolCalls = toolCallsFrom(events);
  const planQuery = useQuery({
    queryKey: queryKeys.plan(activeRunId as string),
    queryFn: () => runsApi.getPlan(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
  });
  // 步骤投影不进事件流,单独按 run 拉;终态后不再变,停轮询。
  const stepsQuery = useQuery({
    queryKey: queryKeys.runSteps(activeRunId as string),
    queryFn: () => runsApi.steps(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
    refetchInterval: () =>
      runQuery.data === undefined || isRunTerminal(runQuery.data.status)
        ? false
        : 1500,
  });

  // 任务清单投影:与步骤同频轮询,终态后停;旧 run/没调用过 todo 工具时为空数组。
  const todosQuery = useQuery({
    queryKey: queryKeys.runTodos(activeRunId as string),
    queryFn: () => runsApi.todos(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
    refetchInterval: () =>
      runQuery.data === undefined || isRunTerminal(runQuery.data.status)
        ? false
        : 1500,
  });
  const run = runQuery.data;
  const status: RunStatus = run?.status ?? 'pending';
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
    steps: stepsQuery.data ?? [],
    todos: todosQuery.data?.todos ?? [],
    plan: planQuery.data,
    liveRun,
    runPaused,
    question,
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
