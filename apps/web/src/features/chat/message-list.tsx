import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { FileText, FolderKanban, Plug, Sparkles } from 'lucide-react';
import { Link } from 'react-router';

import { Select } from '@beauty/design';

import { RunArtifacts } from '@/components/console/run-artifacts';
import { Markdown } from '@/components/console/markdown';
import { ToolCallCard } from '@/components/console/tool-call-card';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  ConsoleButton,
  EmptyPanel,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import type { Attachment, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import {
  RUN_PHASE_LABELS,
  clockTime,
  isRunTerminal,
  runMetaLine,
} from '@/lib/presentation';
import type { Run } from '@/lib/runs-api';

import { DangerConfirmCard, PlanApprovalCard } from './approval-block';
import type { ChatActions } from './use-chat-actions';
import type { ChatQueries } from './use-chat-queries';
import type { ChatStream } from './use-chat-stream';

const SUGGESTIONS = [
  '把本周文档整理成一份周报草稿',
  '帮我理一下这周的待办,给出优先级',
  '基于知识库整理一份产品背景资料',
];

export type ChatMessageListProps = {
  queries: ChatQueries;
  actions: ChatActions;
  stream: ChatStream;
  activeConversationId: string | null;
  showArtifact: boolean;
  onToggleArtifact: () => void;
  error: unknown;
  unauthorized: boolean;
};

/**
 * 会话消息流:hero 空态、历史消息、流式气泡、任务实时输出、工具卡、
 * 审批/危险确认卡片、终态提示与错误呈现。纯展示,动作经 props 上抛。
 */
export function ChatMessageList({
  queries,
  actions,
  stream,
  activeConversationId,
  showArtifact,
  onToggleArtifact,
  error,
  unauthorized,
}: ChatMessageListProps) {
  const pending = stream.visiblePending;
  const {
    hasNoWorkspaces,
    workspaces,
    activeWorkspace,
    messages,
    attachmentsByMessage,
    activeRunId,
    run,
    status,
    inFlight,
    toolCalls,
    plan,
    liveRun,
    runPaused,
    eventLines,
    mcpCatalog,
    changeNewSessionWorkspace,
  } = queries;
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'nearest' });
  }, [
    messages.length,
    eventLines.length,
    toolCalls.length,
    liveRun?.text.length,
    pending?.reply.length,
  ]);

  return (
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

        {!hasNoWorkspaces && !activeConversationId && !pending ? (
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
                  onClick={() => void stream.sendStreaming(suggestion)}
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
              message.run_id && message.run_id === activeRunId ? run : undefined
            }
            steps={
              message.run_id && message.run_id === activeRunId
                ? toolCalls.length
                : 0
            }
            mcpCatalog={mcpCatalog}
          />
        ))}

        {pending ? (
          <>
            <UserBlock
              text={pending.user}
              attachments={pending.attachments}
              mcpServers={pending.mcpServers}
            />
            {pending.chatTools.length > 0 ? (
              <ol className="space-y-1.5 border-l border-ha-border pl-3">
                {pending.chatTools.map((call, index) => (
                  <li
                    key={`${call.name}-${index}`}
                    className="flex items-center gap-2 text-[12px] text-ha-dim"
                  >
                    <StatusDot
                      tone={call.ok ? 'success' : 'danger'}
                      pulse={
                        index === pending.chatTools.length - 1 && !pending.reply
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
                pending.chatTools.length
                  ? `查询了 ${pending.chatTools.length} 次`
                  : '正在回答'
              }
              live
            >
              {pending.reply || '正在回答…'}
            </AgentBlock>
          </>
        ) : null}

        {/* 任务执行期的实时正文气泡:phase 变一段、run 终态(inFlight 变 false)
            就整块消失,此时落库的 assistant 消息已通过 invalidate 出现,避免正文重复。 */}
        {inFlight && run && !runPaused ? (
          <div
            role="status"
            aria-label="任务实时输出"
            className="flex flex-col gap-3"
          >
            <AgentBlock
              meta="实时输出"
              live
              badge={
                <ToneBadge tone="info">
                  {RUN_PHASE_LABELS[liveRun?.phase ?? ''] ?? '执行中'}
                </ToneBadge>
              }
            >
              {liveRun?.text || '正在执行…'}
            </AgentBlock>
          </div>
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

        {run && status === 'awaiting_approval' && plan ? (
          <PlanApprovalCard
            plan={plan}
            rejectReason={actions.rejectReason}
            onRejectReasonChange={actions.setRejectReason}
            onApprove={() => actions.approve(run.id)}
            onReject={() => actions.rejectPlan(run.id)}
            busy={actions.busy}
          />
        ) : null}

        {run && status === 'awaiting_confirmation' ? (
          <DangerConfirmCard
            onConfirm={() => actions.confirmDanger(run.id, 'approve')}
            onReject={() => actions.confirmDanger(run.id, 'reject')}
            busy={actions.busy}
          />
        ) : null}

        {run?.status_reason && (status === 'failed' || status === 'blocked') ? (
          <p className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] leading-5 text-ha-danger">
            {status === 'failed' ? '失败原因' : '中断原因'}:{run.status_reason}
            <Link to="/runs" className="ml-2 underline underline-offset-2">
              去任务编排处理
            </Link>
          </p>
        ) : null}

        {status === 'completed' && activeRunId ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <ConsoleActionButton
                showArtifact={showArtifact}
                onToggle={onToggleArtifact}
              />
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
  );
}

/** 完成态下的成品开关按钮(文案随展开状态切换)。 */
function ConsoleActionButton({
  showArtifact,
  onToggle,
}: {
  showArtifact: boolean;
  onToggle: () => void;
}) {
  return (
    <ConsoleButton variant="secondary" size="sm" onClick={onToggle}>
      <FileText className="h-3.5 w-3.5" />
      {showArtifact ? '收起成品' : '查看成品'}
    </ConsoleButton>
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
  badge,
  children,
}: {
  meta: string;
  live?: boolean;
  /** 头部附加标签(如任务阶段),与 meta 同行展示。 */
  badge?: ReactNode;
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
        {badge}
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
