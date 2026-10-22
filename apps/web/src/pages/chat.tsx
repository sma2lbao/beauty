import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowUp,
  FileText,
  FolderKanban,
  Paperclip,
  Plug,
  Sparkles,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { Select } from '@beauty/design';

import { ExecutionPanel } from '@/components/console/execution-panel';
import { ConsoleStatusBar } from '@/components/console/status-bar';
import { ToolCallCard } from '@/components/console/tool-call-card';
import { ConsoleTopbar } from '@/components/console/topbar';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  ConsoleButton,
  EmptyPanel,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import { Markdown } from '@/components/console/markdown';
import { RunArtifacts } from '@/components/console/run-artifacts';
import { useRunEvents } from '@/components/runs/use-run-events';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { mcpApi } from '@/lib/mcp-api';
import { skillsApi } from '@/lib/skills-api';
import type {
  Attachment,
  ChatMessage,
  SendMode,
  StreamMessageHandlers,
} from '@/lib/conversations-api';
import {
  RUN_STATUS_LABELS,
  clockTime,
  eventLine,
  isRunTerminal,
  runMetaLine,
  runStatusTone,
  toolCallsFrom,
} from '@/lib/presentation';
import type { Run, RunStatus } from '@/lib/runs-api';
import { isUnauthorized, runsApi } from '@/lib/runs-api';
import { useWorkspaceSelection } from '@/lib/workspace-selection';
import { useUiStore } from '@/stores/ui-store';

const SUGGESTIONS = [
  '把本周文档整理成一份周报草稿',
  '帮我理一下这周的待办,给出优先级',
  '基于知识库整理一份产品背景资料',
];

/**
 * 工作台(首页):设计稿《01 主控台 · 会话工作台》。
 *
 * 行为保持原样(不为了换皮改协议):
 * - 发送走 SSE 流:**meta** 告知分流结果,**delta** 让直接回答的正文边产出边渲染;
 * - 复杂任务才建 run:进行中则并入追加指令;任务的实时行动来自 run 的 SSE;
 * - 右侧执行面板把 run 事件翻成「执行步骤 / 变更 / 终端」;
 * - 实时过程与工具调用都是**读**事件流水,不改变任何状态语义。
 */
export function ChatPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');
  const newFlag = searchParams.get('new') === '1';
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

  const [draft, setDraft] = useState('');
  const [sendMode, setSendMode] = useState<SendMode>('auto');
  const skillParam = searchParams.get('skill');
  const [skillOverride, setSkillOverride] = useState<string | null>(null);
  // URL 里的 skill 是默认值;用户手动切换后以本地选择为准(不做 effect 同步)
  const selectedSkill = skillOverride ?? skillParam ?? '';
  const mcpParam = searchParams.get('mcp');
  const [mcpOverride, setMcpOverride] = useState<string | null>(null);
  // 与技能同规则:?mcp=<id> 是默认引用,手动切换后以本地选择为准
  const selectedMcp = mcpOverride ?? mcpParam ?? '';
  // 会话选择以 URL 为唯一来源:侧边栏/新建任务都改地址栏,组件不再另存一份
  const activeConversationId = newFlag ? null : conversationParam;
  // 瞬时态都带上会话 id:切走再切回不会把上一个会话的流/错误/气泡串过来
  const [manualRun, setManualRun] = useState<{
    conversationId: string;
    runId: string;
  } | null>(null);
  const [rejectReason, setRejectReason] = useState('先别发,我要改一下');
  const [artifactFor, setArtifactFor] = useState<string | null>(null);
  const [pending, setPending] = useState<{
    conversationId: string;
    user: string;
    reply: string;
    attachments: Attachment[];
    /** 本次发送显式引用的 MCP server(展示 chip 用;不选为空数组) */
    mcpServers: { id: string; name: string }[];
    /** 会话内只读工具调用(tool 事件流式累积;done 后随消息一起消失) */
    chatTools: { name: string; arguments: string; ok: boolean }[];
  } | null>(null);
  const [draftAttachments, setDraftAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [streamError, setStreamError] = useState<{
    conversationId: string;
    error: Error;
  } | null>(null);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const prevStatusRef = useRef<RunStatus | null>(null);

  const conversationsQuery = useQuery({
    queryKey: ['conversations', activeWorkspaceId],
    queryFn: () =>
      conversationsApi.list({
        workspace_id: activeWorkspaceId ?? undefined,
      }),
    enabled: Boolean(activeWorkspaceId),
  });
  const skillsQuery = useQuery({
    queryKey: ['skills'],
    queryFn: skillsApi.list,
    retry: false,
  });
  const mcpQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: mcpApi.list,
    retry: false,
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
    queryKey: ['conversation', activeConversationId],
    queryFn: () => conversationsApi.get(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });
  const conversationWorkspaceId = conversationQuery.data?.workspace_id ?? null;

  const messagesQuery = useQuery({
    queryKey: ['conversation-messages', activeConversationId],
    queryFn: () => conversationsApi.messages(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });
  const messages = messagesQuery.data ?? [];

  const attachmentsQuery = useQuery({
    queryKey: ['conversation-attachments', activeConversationId],
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
    queryKey: ['run', activeRunId],
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
    queryKey: ['plan', activeRunId],
    queryFn: () => runsApi.getPlan(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
  });
  const showArtifact =
    Boolean(activeConversationId) && artifactFor === activeConversationId;

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
  const visiblePending =
    pending && pending.conversationId === activeConversationId ? pending : null;
  const visibleError =
    streamError &&
    (streamError.conversationId === activeConversationId ||
      streamError.conversationId === '')
      ? streamError.error
      : null;

  const settle = async (conversationId: string) => {
    await queryClient.invalidateQueries({
      queryKey: ['conversation-messages', conversationId],
    });
    await queryClient.invalidateQueries({
      queryKey: ['conversation-attachments', conversationId],
    });
    await queryClient.invalidateQueries({ queryKey: ['conversations'] });
    setPending((previous) =>
      previous && previous.conversationId === conversationId ? null : previous,
    );
  };

  const uploadAttachments = async (files: FileList | null) => {
    if (!files?.length || !activeConversationId) return;
    setUploading(true);
    setAttachmentError(null);
    const selected = [...draftAttachments];
    try {
      for (const file of Array.from(files)) {
        if (selected.length >= 5) break;
        if (file.size > 10 * 1024 * 1024) {
          throw new Error('附件超过 10MB 上限');
        }
        selected.push(
          await conversationsApi.uploadAttachment(activeConversationId, file),
        );
      }
      setDraftAttachments(selected);
    } catch (failure) {
      setAttachmentError(
        failure instanceof Error ? failure.message : '附件上传失败',
      );
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const sendStreaming = async (text: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    const selectedAttachments = draftAttachments;
    setStreamError(null);
    setSending(true);
    setDraft('');
    setDraftAttachments([]);
    setArtifactFor(null);
    let conversationId = activeConversationId;
    try {
      if (!conversationId) {
        const created = await conversationsApi.create({
          workspace_id: activeWorkspaceId,
        });
        conversationId = created.id;
        // 新会话 id 由下方 effect 写进 URL(单点负责,避免与工作区写回互相覆盖)
        setFreshConversationId(created.id);
      }
      const targetConversationId = conversationId;
      const mcpServerIds = selectedMcp ? [selectedMcp] : [];
      setPending({
        conversationId: targetConversationId,
        user: text,
        reply: '',
        attachments: selectedAttachments,
        mcpServers: mcpServerIds.map((id) => ({
          id,
          name: mcpServerName(id),
        })),
        chatTools: [],
      });
      const streamHandlers: StreamMessageHandlers = {
        signal: controller.signal,
        onMeta: (meta) => {
          if (meta.run) {
            setManualRun({
              conversationId: targetConversationId,
              runId: meta.run.id,
            });
          }
          void queryClient.invalidateQueries({ queryKey: ['conversations'] });
          void queryClient.invalidateQueries({
            queryKey: ['conversation', targetConversationId],
          });
        },
        onDelta: (delta) =>
          setPending((previous) =>
            previous && previous.conversationId === targetConversationId
              ? { ...previous, reply: previous.reply + delta }
              : previous,
          ),
        onTool: (call) =>
          setPending((previous) =>
            previous && previous.conversationId === targetConversationId
              ? { ...previous, chatTools: [...previous.chatTools, call] }
              : previous,
          ),
        onDone: () => {
          void settle(targetConversationId);
        },
        onError: (failure) => {
          setStreamError({
            conversationId: targetConversationId,
            error: new Error(failure.message),
          });
          void settle(targetConversationId);
        },
      };
      const attachmentIds = selectedAttachments.map(
        (attachment) => attachment.id,
      );
      // 不选的项照旧不传参数,保证原有调用形状(skill 为空、无 MCP 时只有 5 个参数)
      if (mcpServerIds.length) {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
          selectedSkill || undefined,
          mcpServerIds,
        );
      } else if (selectedSkill) {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
          selectedSkill,
        );
      } else {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
        );
      }
    } catch (failure) {
      if ((failure as Error)?.name !== 'AbortError') {
        const error =
          failure instanceof Error ? failure : new Error('发送失败');
        setStreamError({ conversationId: conversationId ?? '', error });
        setDraft(text);
        setDraftAttachments(selectedAttachments);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  const action = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', activeRunId] }),
        queryClient.invalidateQueries({ queryKey: ['conversations'] }),
      ]);
    },
  });

  // 通知 watcher 用:声明"用户正盯着的会话",该会话的状态变化只记录不弹 toast
  const setCurrentConversation = useUiStore(
    (state) => state.setCurrentConversation,
  );
  useEffect(() => {
    setCurrentConversation(activeConversationId);
    return () => setCurrentConversation(null);
  }, [activeConversationId, setCurrentConversation]);

  // 新会话创建后把 id 写进 URL。两个约束:
  // 1) 必须在 effect 里:useSearchParams 的函数式 updater 读的是"当次渲染"的
  //    searchParams,异步回调里读到的是发送前的旧值,会与"默认工作区写回 URL"
  //    的 effect 互相覆盖(表现为新会话冒出来又消失);
  // 2) 用独立 state 记住这个 id 而不是 pending——流很快结束时 pending 会被清掉,
  //    此时若 URL 被同批渲染里另一个 effect 覆盖,就没机会补回来。
  // effect 每次渲染都拿到最新 searchParams,双方各补自己缺的参数,最多两轮收敛。
  const [freshConversationId, setFreshConversationId] = useState<string | null>(
    null,
  );
  useEffect(() => {
    if (!freshConversationId) {
      return;
    }
    if (conversationParam === freshConversationId) {
      setFreshConversationId(null);
      return;
    }
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (activeWorkspaceId) next.set('workspace', activeWorkspaceId);
        next.set('conversation', freshConversationId);
        next.delete('new');
        return next;
      },
      { replace: true },
    );
  }, [
    activeWorkspaceId,
    conversationParam,
    freshConversationId,
    setSearchParams,
  ]);

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
        queryKey: ['conversation-messages', activeConversationId],
      });
      void queryClient.invalidateQueries({ queryKey: ['conversations'] });
    }
  }, [status, activeConversationId, queryClient]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'nearest' });
  }, [
    messages.length,
    eventLines.length,
    toolCalls.length,
    visiblePending?.reply.length,
  ]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const error =
    visibleError ??
    action.error ??
    runQuery.error ??
    conversationsQuery.error ??
    conversationQuery.error ??
    messagesQuery.error;
  const unauthorized = isUnauthorized(error);
  const canSend = draft.trim().length > 0 && !sending;

  const submit = () => {
    const text = draft.trim();
    if (!text || sending) return;
    void sendStreaming(text);
  };

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

  const footerAction =
    status === 'awaiting_approval' && run ? (
      <ConsoleButton
        variant="primary"
        size="sm"
        onClick={() => action.mutate(() => runsApi.approve(run.id))}
        disabled={action.isPending}
      >
        放行执行
      </ConsoleButton>
    ) : status === 'awaiting_confirmation' && run ? (
      <ConsoleButton
        variant="primary"
        size="sm"
        onClick={() => action.mutate(() => runsApi.confirm(run.id, 'approve'))}
        disabled={action.isPending}
      >
        确认危险操作
      </ConsoleButton>
    ) : status === 'completed' ? (
      <ConsoleButton
        variant="secondary"
        size="sm"
        onClick={() =>
          setArtifactFor(showArtifact ? null : activeConversationId)
        }
      >
        查看成品
      </ConsoleButton>
    ) : status === 'running' ? (
      <ConsoleButton
        variant="secondary"
        size="sm"
        disabled
        title="暂停能力暂未开放"
      >
        暂停执行
      </ConsoleButton>
    ) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ConsoleTopbar
        title={title}
        status={activeRunId ? status : null}
        workspace={activeWorkspace?.name ?? null}
      />

      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col bg-ha-bg">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 lg:px-12">
            <div className="mx-auto flex w-full max-w-[820px] flex-col gap-6">
              {hasNoWorkspaces ? (
                <div className="flex min-h-[52vh] items-center justify-center">
                  <EmptyPanel
                    className="w-full max-w-xl"
                    icon={<FolderKanban className="h-7 w-7" />}
                    title="还没有工作区"
                    description="会话必须归属于工作区:先创建一个工作区"
                    action={
                      <Link
                        to="/workspace?new=1"
                        className={consoleButtonClass({
                          variant: 'primary',
                          size: 'sm',
                        })}
                      >
                        去创建工作区
                      </Link>
                    }
                  />
                </div>
              ) : null}

              {!hasNoWorkspaces && !activeConversationId && !visiblePending ? (
                <div className="flex flex-col items-center gap-4 py-12 text-center">
                  <span className="grid h-11 w-11 place-items-center rounded-xl border border-ha-border bg-[#1f2333]">
                    <Sparkles className="h-5 w-5 text-ha-accent-soft" />
                  </span>
                  <div>
                    <h2 className="text-[17px] font-semibold text-ha-text">
                      开始一个新会话
                    </h2>
                    <p className="mx-auto mt-2 max-w-md text-[13px] leading-6 text-ha-muted">
                      描述要完成的事。简单的直接回答;复杂任务会先给计划,
                      等你放行再执行,危险操作会单独确认。
                    </p>
                    {activeWorkspace ? (
                      <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
                        <label
                          htmlFor="session-workspace"
                          className="font-mono text-[11px] text-ha-faint"
                        >
                          保存到
                        </label>
                        <Select
                          id="session-workspace"
                          size="sm"
                          aria-label="选择工作区"
                          value={activeWorkspace.id}
                          onChange={(event) =>
                            changeNewSessionWorkspace(event.target.value)
                          }
                        >
                          {workspaces.map((workspace) => (
                            <option key={workspace.id} value={workspace.id}>
                              {workspace.name}
                            </option>
                          ))}
                        </Select>
                      </div>
                    ) : null}
                    {activeWorkspace ? (
                      <p className="mx-auto mt-2 max-w-md text-[12px] leading-5 text-ha-dim">
                        会话将保存在「{activeWorkspace.name}」中。
                      </p>
                    ) : null}
                  </div>
                  <div className="flex max-w-lg flex-wrap justify-center gap-2">
                    {SUGGESTIONS.map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => void sendStreaming(suggestion)}
                        className="rounded-md border border-ha-border bg-ha-panel px-3 py-2 text-left text-[12px] text-ha-text-2 transition-colors hover:border-ha-border-strong hover:text-ha-text"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {messages.map((message) => (
                <MessageBlock
                  key={message.id}
                  message={message}
                  attachments={attachmentsByMessage.get(message.id)}
                  run={
                    message.run_id && message.run_id === activeRunId
                      ? run
                      : undefined
                  }
                  steps={
                    message.run_id && message.run_id === activeRunId
                      ? toolCalls.length
                      : 0
                  }
                  mcpCatalog={mcpCatalog}
                />
              ))}

              {visiblePending ? (
                <>
                  <UserBlock
                    text={visiblePending.user}
                    attachments={visiblePending.attachments}
                    mcpServers={visiblePending.mcpServers}
                  />
                  {visiblePending.chatTools.length > 0 ? (
                    <ol className="space-y-1.5 border-l border-ha-border pl-3">
                      {visiblePending.chatTools.map((call, index) => (
                        <li
                          key={`${call.name}-${index}`}
                          className="flex items-center gap-2 text-[12px] text-ha-dim"
                        >
                          <StatusDot
                            tone={call.ok ? 'success' : 'danger'}
                            pulse={
                              index === visiblePending.chatTools.length - 1 &&
                              !visiblePending.reply
                            }
                          />
                          {call.ok
                            ? `调用工具 ${call.name}`
                            : `工具 ${call.name} 被拒绝(会话中只允许只读操作)`}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  <AgentBlock
                    meta={
                      visiblePending.chatTools.length
                        ? `查询了 ${visiblePending.chatTools.length} 次`
                        : '正在回答'
                    }
                    live
                  >
                    {visiblePending.reply || '正在回答…'}
                  </AgentBlock>
                </>
              ) : null}

              {eventLines.length > 0 ? (
                <ol className="space-y-1.5 border-l border-ha-border pl-3">
                  {eventLines.map(({ event, line }) => (
                    <li
                      key={event.seq}
                      className="flex items-center gap-2 text-[12px] text-ha-dim"
                    >
                      <StatusDot
                        tone={line.tone === 'neutral' ? 'info' : line.tone}
                        pulse={status === 'running'}
                      />
                      {line.text}
                    </li>
                  ))}
                </ol>
              ) : null}

              {toolCalls.length > 0 ? (
                <div className="flex flex-col gap-2.5">
                  {toolCalls.map((call, index) => (
                    <ToolCallCard
                      key={call.seq}
                      call={call}
                      live={
                        (status === 'running' || status === 'planning') &&
                        index === toolCalls.length - 1 &&
                        call.ok
                      }
                    />
                  ))}
                </div>
              ) : null}

              {run && status === 'awaiting_approval' && planQuery.data ? (
                <div className="rounded-lg border border-ha-warning-border bg-ha-warning-bg/40">
                  <div className="flex items-center justify-between gap-3 border-b border-ha-warning-border/60 px-3 py-2.5">
                    <span className="text-[12px] font-semibold text-ha-warning">
                      待放行的计划
                    </span>
                    <ToneBadge tone="warning">等待审批</ToneBadge>
                  </div>
                  <div className="max-h-64 overflow-auto px-3 py-2">
                    <Markdown content={planQuery.data.plan_markdown} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2 border-t border-ha-warning-border/60 px-3 py-2.5">
                    <ConsoleButton
                      variant="primary"
                      size="sm"
                      onClick={() =>
                        action.mutate(() => runsApi.approve(run.id))
                      }
                      disabled={action.isPending}
                    >
                      放行执行
                    </ConsoleButton>
                    <ConsoleButton
                      variant="danger"
                      size="sm"
                      onClick={() =>
                        action.mutate(() =>
                          runsApi.reject(run.id, rejectReason),
                        )
                      }
                      disabled={action.isPending}
                    >
                      拒绝计划
                    </ConsoleButton>
                    <input
                      aria-label="拒绝原因"
                      value={rejectReason}
                      onChange={(event) => setRejectReason(event.target.value)}
                      className="h-7 min-w-40 flex-1 rounded-md border border-ha-border-strong bg-ha-panel px-2.5 text-[12px] text-ha-text-body outline-none placeholder:text-ha-dim focus:border-ha-accent"
                    />
                  </div>
                </div>
              ) : null}

              {run && status === 'awaiting_confirmation' ? (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-ha-warning-border bg-ha-warning-bg px-3 py-3">
                  <AlertTriangle className="h-4 w-4 text-ha-warning" />
                  <span className="mr-auto text-[12px] text-ha-text-2">
                    有危险操作需要你确认(例如对外发布),确认后才会执行。
                  </span>
                  <ConsoleButton
                    variant="primary"
                    size="sm"
                    onClick={() =>
                      action.mutate(() => runsApi.confirm(run.id, 'approve'))
                    }
                    disabled={action.isPending}
                  >
                    确认并执行
                  </ConsoleButton>
                  <ConsoleButton
                    variant="danger"
                    size="sm"
                    onClick={() =>
                      action.mutate(() => runsApi.confirm(run.id, 'reject'))
                    }
                    disabled={action.isPending}
                  >
                    拒绝危险操作
                  </ConsoleButton>
                </div>
              ) : null}

              {run?.status_reason &&
              (status === 'failed' || status === 'blocked') ? (
                <p className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] leading-5 text-ha-danger">
                  {status === 'failed' ? '失败原因' : '中断原因'}:
                  {run.status_reason}
                  <Link
                    to="/runs"
                    className="ml-2 underline underline-offset-2"
                  >
                    去任务编排处理
                  </Link>
                </p>
              ) : null}

              {status === 'completed' && activeRunId ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <ConsoleButton
                      variant="secondary"
                      size="sm"
                      onClick={() =>
                        setArtifactFor(
                          showArtifact ? null : activeConversationId,
                        )
                      }
                    >
                      <FileText className="h-3.5 w-3.5" />
                      {showArtifact ? '收起成品' : '查看成品'}
                    </ConsoleButton>
                  </div>
                  {showArtifact && activeRunId ? (
                    <RunArtifacts runId={activeRunId} />
                  ) : null}
                </div>
              ) : null}

              {isRunTerminal(status) && activeRunId ? (
                <p className="text-[12px] text-ha-dim">
                  需要重试、恢复或看完整审计流水?去
                  <Link
                    className="mx-1 text-ha-accent-soft underline underline-offset-2"
                    to="/runs"
                  >
                    任务编排
                  </Link>
                  。
                </p>
              ) : null}

              {error && !unauthorized ? (
                <p className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger">
                  {error instanceof Error ? error.message : '发送失败'}
                </p>
              ) : null}

              {unauthorized ? (
                <p className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning">
                  未登录或令牌失效:请先
                  <Link className="mx-1 underline" to="/login">
                    登录
                  </Link>
                  再开始会话。
                </p>
              ) : null}

              <div ref={bottomRef} />
            </div>
          </div>

          {hasNoWorkspaces ? null : (
            <div className="shrink-0 px-4 pb-5 sm:px-8 lg:px-12">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  submit();
                }}
                className="mx-auto w-full max-w-[820px] rounded-[10px] border border-ha-border-strong bg-ha-panel p-3 transition-colors focus-within:border-ha-accent/70"
              >
                {draftAttachments.length ? (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {draftAttachments.map((attachment) => (
                      <span
                        key={attachment.id}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-1 font-mono text-[11px] text-ha-text-2"
                      >
                        <Paperclip className="h-3 w-3 shrink-0 text-ha-muted" />
                        <span className="truncate">{attachment.filename}</span>
                        <button
                          type="button"
                          aria-label={`移除附件 ${attachment.filename}`}
                          onClick={() =>
                            setDraftAttachments((previous) =>
                              previous.filter(
                                (item) => item.id !== attachment.id,
                              ),
                            )
                          }
                          className="text-ha-dim hover:text-ha-text"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
                {attachmentError ? (
                  <p className="mb-2 text-[12px] text-ha-danger" role="alert">
                    {attachmentError}
                  </p>
                ) : null}
                <label className="sr-only" htmlFor="chat-input">
                  描述下一步任务
                </label>
                <textarea
                  id="chat-input"
                  rows={2}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter' &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      submit();
                    }
                  }}
                  placeholder={
                    inFlight
                      ? '补充一句,会并入当前任务的追加指令…'
                      : '描述下一步任务,或直接说说你想做什么…'
                  }
                  className="block w-full resize-none bg-transparent px-1 py-0.5 text-[13px] leading-6 text-ha-text-body outline-none placeholder:text-ha-dim"
                />
                <div className="mt-2 flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      className="hidden"
                      accept=".txt,.md,.markdown,.csv,.json,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp"
                      onChange={(event) =>
                        void uploadAttachments(event.target.files)
                      }
                    />
                    <button
                      type="button"
                      aria-label="添加附件"
                      title={
                        activeConversationId
                          ? '添加附件(文本/Markdown/CSV/JSON/PDF/docx/xlsx/图片,单个 ≤10MB)'
                          : '先发一条消息创建会话,再添加附件'
                      }
                      disabled={
                        !activeConversationId ||
                        uploading ||
                        draftAttachments.length >= 5
                      }
                      onClick={() => fileInputRef.current?.click()}
                      className="grid h-6 w-6 place-items-center rounded text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:opacity-40"
                    >
                      <Paperclip className="h-3.5 w-3.5" />
                    </button>
                    <label
                      htmlFor="send-mode"
                      className="font-mono text-[11px] text-ha-faint"
                    >
                      发送方式
                    </label>
                    <select
                      id="send-mode"
                      value={sendMode}
                      onChange={(event) =>
                        setSendMode(event.target.value as SendMode)
                      }
                      className="h-[22px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
                    >
                      <option value="auto">自动分流</option>
                      <option value="chat">直接回答</option>
                      <option value="task">作为任务</option>
                    </select>
                    <label
                      htmlFor="send-skill"
                      className="font-mono text-[11px] text-ha-faint"
                    >
                      技能
                    </label>
                    <select
                      id="send-skill"
                      aria-label="选择技能"
                      value={selectedSkill}
                      onChange={(event) => setSkillOverride(event.target.value)}
                      className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
                    >
                      <option value="">不使用技能</option>
                      {(skillsQuery.data ?? []).map((skill) => (
                        <option key={skill.id} value={skill.id}>
                          {skill.title}
                        </option>
                      ))}
                    </select>
                    <label
                      htmlFor="send-mcp"
                      className="font-mono text-[11px] text-ha-faint"
                    >
                      MCP
                    </label>
                    <select
                      id="send-mcp"
                      aria-label="选择 MCP server"
                      value={selectedMcp}
                      onChange={(event) => setMcpOverride(event.target.value)}
                      className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
                    >
                      <option value="">不使用 MCP</option>
                      {enabledMcpServers.map((server) => (
                        <option key={server.id} value={server.id}>
                          {server.name}
                        </option>
                      ))}
                      {/* URL/草稿引用了已停用或已删除的 server:补一个兜底项,避免 value 失配 */}
                      {selectedMcp &&
                      !enabledMcpServers.some(
                        (server) => server.id === selectedMcp,
                      ) ? (
                        <option value={selectedMcp}>
                          {mcpServerName(selectedMcp)}
                        </option>
                      ) : null}
                    </select>
                    {inFlight ? (
                      <span className="hidden truncate font-mono text-[11px] text-ha-info sm:inline">
                        任务进行中 · 发送会并入追加指令
                      </span>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {sending ? (
                      <button
                        type="button"
                        onClick={() => abortRef.current?.abort()}
                        className="font-mono text-[11px] text-ha-dim hover:text-ha-text-2"
                      >
                        停止生成
                      </button>
                    ) : null}
                    <button
                      type="submit"
                      aria-label="发送"
                      disabled={!canSend}
                      className="grid h-7 w-7 place-items-center rounded-md bg-ha-accent text-white transition-colors hover:bg-[#6b77e2] disabled:opacity-40"
                    >
                      <ArrowUp className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </form>
              <p className="mx-auto mt-2 max-w-[820px] text-center font-mono text-[11px] text-ha-faint">
                Enter 发送 · Shift + Enter 换行
              </p>
            </div>
          )}
        </section>

        <ExecutionPanel
          run={run}
          status={status}
          events={events}
          toolCalls={toolCalls}
          connected={connected}
          reconnecting={reconnecting}
          runId={activeRunId}
          footerAction={footerAction}
        />
      </div>

      <ConsoleStatusBar
        items={[
          { label: `事件 ${events.length}` },
          {
            label: connected
              ? 'SSE 已连接'
              : reconnecting
                ? 'SSE 重连中'
                : 'SSE 未连接',
            tone: connected ? 'success' : reconnecting ? 'warning' : undefined,
          },
          ...(activeRunId
            ? [
                {
                  label: `运行 #${activeRunId.slice(0, 8)}`,
                },
              ]
            : []),
          ...(activeRunId
            ? [
                {
                  label: RUN_STATUS_LABELS[status],
                  tone:
                    runStatusTone(status) === 'neutral'
                      ? undefined
                      : runStatusTone(status),
                },
              ]
            : []),
        ]}
        right="beauty moon · 工作台"
      />
    </div>
  );
}

/* ── 消息块 ─────────────────────────────────────────────────────────── */

function UserBlock({
  text,
  createdAt,
  attachments = [],
  mcpServers = [],
}: {
  text: string;
  createdAt?: string;
  attachments?: Attachment[];
  mcpServers?: { id: string; name: string }[];
}) {
  return (
    <article>
      <header className="flex items-center gap-2">
        <span className="text-[11px] font-semibold text-ha-dim">你</span>
        {createdAt ? (
          <span className="font-mono text-[11px] text-ha-faint">
            {clockTime(createdAt)}
          </span>
        ) : null}
      </header>
      <p className="mt-2 whitespace-pre-wrap text-[14px] leading-[23px] text-ha-text-body">
        {text}
      </p>
      {mcpServers.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {mcpServers.map((server) => (
            <span
              key={server.id}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-text-2"
            >
              <Plug className="h-3 w-3 shrink-0 text-ha-muted" />
              <span className="truncate">MCP · {server.name}</span>
            </span>
          ))}
        </div>
      ) : null}
      {attachments.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {attachments.map((attachment) => (
            <button
              key={attachment.id}
              type="button"
              onClick={() => void downloadAttachmentFile(attachment)}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel px-2 py-1 font-mono text-[11px] text-ha-text-2 transition-colors hover:border-ha-border-strong hover:text-ha-text"
            >
              <FileText className="h-3 w-3 shrink-0 text-ha-muted" />
              <span className="truncate">{attachment.filename}</span>
              <span className="shrink-0 text-ha-faint">
                {formatAttachmentSize(attachment.size_bytes)}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function formatAttachmentSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

async function downloadAttachmentFile(attachment: Attachment): Promise<void> {
  try {
    const blob = await conversationsApi.downloadAttachment(attachment.id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = attachment.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch {
    // 下载失败时静默;下次点击可重试
  }
}

function AgentBlock({
  meta,
  live = false,
  children,
}: {
  meta: string;
  live?: boolean;
  children: string;
}) {
  return (
    <article className="flex flex-col gap-3">
      <header className="flex items-center gap-2">
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[#1f2333]">
          <Sparkles className="h-3 w-3 text-ha-accent-soft" />
        </span>
        <span className="text-[13px] font-semibold text-ha-text">
          beauty moon Agent
        </span>
        <span className="font-mono text-[11px] text-ha-dim">{meta}</span>
      </header>
      <Markdown content={children} streaming={live} />
    </article>
  );
}

function MessageBlock({
  message,
  run,
  steps,
  attachments,
  mcpCatalog = [],
}: {
  message: ChatMessage;
  run?: Run;
  steps?: number;
  attachments?: Attachment[];
  mcpCatalog?: { id: string; name: string }[];
}) {
  if (message.role === 'user') {
    // 消息只存 id;名字用当前 server 列表映射,列表里没有(已删除)则退回 id
    const mcpServers = (message.mcp_server_ids ?? []).map((id) => ({
      id,
      name: mcpCatalog.find((server) => server.id === id)?.name ?? id,
    }));
    return (
      <UserBlock
        text={message.content}
        createdAt={message.created_at}
        attachments={attachments}
        mcpServers={mcpServers}
      />
    );
  }
  const meta =
    run && steps
      ? `已执行 ${steps} 步`
      : run
        ? runMetaLine(run)
        : 'beauty moon Agent';
  return <AgentBlock meta={meta}>{message.content}</AgentBlock>;
}
