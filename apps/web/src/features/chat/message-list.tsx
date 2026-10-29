import { FileText } from 'lucide-react';
import { Link } from 'react-router';

import { RunArtifacts } from '@/features/runs/run-artifacts';
import { ToolCallCard } from '@/features/chat/tool-call-card';
import { ConsoleButton, StatusDot, ToneBadge } from '@/components/console/ui';
import { RUN_PHASE_LABELS, isRunTerminal } from '@/lib/presentation';

import { DangerConfirmCard, PlanApprovalCard } from './approval-block';
import { ChatEmptyState } from './chat-empty-state';
import { ChatPendingBlock } from './chat-pending-block';
import { AgentBlock, MessageBlock } from './message-bubbles';
import { QuestionCard } from './question-card';
import type { ChatActions } from './use-chat-actions';
import type { ChatQueries } from './use-chat-queries';
import type { ChatStream } from './use-chat-stream';
import { useMessageListScroll } from './use-message-list-scroll';

export type ChatMessageListProps = {
  queries: ChatQueries;
  actions: ChatActions;
  stream: ChatStream;
  activeConversationId: string | null;
  showArtifact: boolean;
  onToggleArtifact: () => void;
  /** 点工具卡路径时打开文件查看器(由页面提供)。 */
  onOpenFile?: (path: string) => void;
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
  onOpenFile,
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
    question,
    eventLines,
    mcpCatalog,
    changeNewSessionWorkspace,
  } = queries;

  const bottomRef = useMessageListScroll({
    messagesCount: messages.length,
    eventLinesCount: eventLines.length,
    toolCallsCount: toolCalls.length,
    liveRunTextLength: liveRun?.text.length,
    pendingReplyLength: pending?.reply.length,
    questionPhase: question?.phase,
    questionId: question?.question.question_id,
  });

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 lg:px-12">
      <div className="mx-auto flex w-full max-w-[820px] flex-col gap-6">
        <ChatEmptyState
          hasNoWorkspaces={hasNoWorkspaces}
          showHero={!hasNoWorkspaces && !activeConversationId && !pending}
          workspaces={workspaces}
          activeWorkspace={activeWorkspace}
          onChangeWorkspace={(workspaceId) =>
            changeNewSessionWorkspace(workspaceId)
          }
          onSuggestion={(suggestion) => void stream.sendStreaming(suggestion)}
        />

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

        {pending ? <ChatPendingBlock pending={pending} /> : null}

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
                onOpenFile={onOpenFile}
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

        {run && question ? (
          <QuestionCard
            key={question.question.question_id}
            state={question}
            busy={actions.busy}
            onSubmit={async (payload) => {
              await actions.answerQuestion(run.id, payload);
            }}
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
