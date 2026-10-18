import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowUp,
  Download,
  FileText,
  Paperclip,
  Sparkles,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ExecutionPanel } from '@/components/console/execution-panel';
import { ConsoleStatusBar } from '@/components/console/status-bar';
import { ToolCallCard } from '@/components/console/tool-call-card';
import { ConsoleTopbar } from '@/components/console/topbar';
import { ConsoleButton, StatusDot, ToneBadge } from '@/components/console/ui';
import { ArtifactPreview } from '@/components/console/artifact-preview';
import { Markdown } from '@/components/console/markdown';
import { useRunEvents } from '@/components/runs/use-run-events';
import { downloadArtifact } from '@/lib/artifacts';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
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

const SUGGESTIONS = [
  '把本周邮件整理成一份周报草稿',
  '帮我理一下这周的待办,给出优先级',
  '起草一封给合作方的项目进度同步邮件',
];

/**
 * 工作台(首页):设计稿《01 主控台 · 会话工作台》。
 *
 * 行为保持原样(不为了换皮改协议):
 * - 发送走 SSE 流:**meta** 告知分流结果,**delta** 让直接对话的正文边产出边渲染;
 * - 复杂任务才建 run:进行中则并入追加指令;任务的实时行动来自 run 的 SSE;
 * - 右侧执行面板把 run 事件翻成「执行步骤 / 变更 / 终端」;
 * - 实时过程与工具调用都是**读**事件流水,不改变任何状态语义。
 */
export function ChatPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');
  const newFlag = searchParams.get('new') === '1';

  const [draft, setDraft] = useState('');
  const [sendMode, setSendMode] = useState<SendMode>('auto');
  const skillParam = searchParams.get('skill');
  const [selectedSkill, setSelectedSkill] = useState(skillParam ?? '');
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
    queryKey: ['conversations'],
    queryFn: () => conversationsApi.list(),
  });
  const skillsQuery = useQuery({
    queryKey: ['skills'],
    queryFn: skillsApi.list,
    retry: false,
  });
  const conversations = conversationsQuery.data ?? [];

  const conversationQuery = useQuery({
    queryKey: ['conversation', activeConversationId],
    queryFn: () => conversationsApi.get(activeConversationId as string),
    enabled: Boolean(activeConversationId),
  });

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
  const artifactQuery = useQuery({
    queryKey: ['artifact', activeRunId],
    queryFn: () => runsApi.artifact(activeRunId as string),
    enabled: Boolean(activeRunId) && showArtifact,
    retry: false,
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
        conversationId = (await conversationsApi.create()).id;
        setSearchParams({ conversation: conversationId }, { replace: true });
      }
      const targetConversationId = conversationId;
      setPending({
        conversationId: targetConversationId,
        user: text,
        reply: '',
        attachments: selectedAttachments,
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
      if (selectedSkill) {
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

  useEffect(() => {
    if (skillParam) setSelectedSkill(skillParam);
  }, [skillParam]);

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
        确认写操作
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
      <ConsoleTopbar title={title} status={activeRunId ? status : null} />

      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col bg-ha-bg">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 lg:px-12">
            <div className="mx-auto flex w-full max-w-[820px] flex-col gap-6">
              {!activeConversationId && !visiblePending ? (
                <div className="flex flex-col items-center gap-4 py-12 text-center">
                  <span className="grid h-11 w-11 place-items-center rounded-xl border border-ha-border bg-[#1f2333]">
                    <Sparkles className="h-5 w-5 text-ha-accent-soft" />
                  </span>
                  <div>
                    <h2 className="text-[17px] font-semibold text-ha-text">
                      开始一个新任务
                    </h2>
                    <p className="mx-auto mt-2 max-w-md text-[13px] leading-6 text-ha-muted">
                      描述要完成的事。简单的直接回答;复杂任务会先给计划,
                      等你放行再执行,写操作会单独确认。
                    </p>
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
                />
              ))}

              {visiblePending ? (
                <>
                  <UserBlock
                    text={visiblePending.user}
                    attachments={visiblePending.attachments}
                  />
                  <AgentBlock
                    meta={
                      toolCalls.length
                        ? `已执行 ${toolCalls.length} 步`
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
                    有写操作需要你确认(例如发信),确认后才会执行。
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
                    拒绝写操作
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
                    {artifactQuery.data ? (
                      <ToneBadge tone="success">
                        {artifactQuery.data.kind}
                      </ToneBadge>
                    ) : null}
                  </div>
                  {showArtifact ? (
                    artifactQuery.isLoading ? (
                      <p className="text-[12px] text-ha-dim">读取中…</p>
                    ) : artifactQuery.data ? (
                      <div className="overflow-hidden rounded-lg border border-ha-border bg-ha-code">
                        <div className="flex items-center justify-between gap-3 border-b border-ha-border px-3 py-2">
                          <span className="truncate font-mono text-[12px] text-ha-text-2">
                            {artifactQuery.data.title}
                          </span>
                          <div className="flex shrink-0 items-center gap-2">
                            <span className="font-mono text-[11px] text-ha-faint">
                              {artifactQuery.data.content.length} 字符
                            </span>
                            <button
                              type="button"
                              aria-label="下载成品"
                              onClick={() =>
                                downloadArtifact(artifactQuery.data)
                              }
                              className="grid h-6 w-6 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
                            >
                              <Download className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                        <div className="max-h-72 overflow-auto p-3">
                          <ArtifactPreview artifact={artifactQuery.data} />
                        </div>
                      </div>
                    ) : (
                      <p className="text-[12px] text-ha-dim">还没有交付物。</p>
                    )
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
                  再开始对话。
                </p>
              ) : null}

              <div ref={bottomRef} />
            </div>
          </div>

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
                    accept=".txt,.md,.markdown,.csv,.json,.pdf,.png,.jpg,.jpeg,.webp"
                    onChange={(event) =>
                      void uploadAttachments(event.target.files)
                    }
                  />
                  <button
                    type="button"
                    aria-label="添加附件"
                    title={
                      activeConversationId
                        ? '添加附件(文本/Markdown/CSV/JSON/PDF/图片,单个 ≤10MB)'
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
                    <option value="chat">直接对话</option>
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
                    onChange={(event) => setSelectedSkill(event.target.value)}
                    className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
                  >
                    <option value="">不使用技能</option>
                    {(skillsQuery.data ?? []).map((skill) => (
                      <option key={skill.id} value={skill.id}>
                        {skill.title}
                      </option>
                    ))}
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
        </section>

        <ExecutionPanel
          run={run}
          status={status}
          events={events}
          toolCalls={toolCalls}
          connected={connected}
          reconnecting={reconnecting}
          artifact={artifactQuery.data ?? null}
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
}: {
  text: string;
  createdAt?: string;
  attachments?: Attachment[];
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
}: {
  message: ChatMessage;
  run?: Run;
  steps?: number;
  attachments?: Attachment[];
}) {
  if (message.role === 'user') {
    return (
      <UserBlock
        text={message.content}
        createdAt={message.created_at}
        attachments={attachments}
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
