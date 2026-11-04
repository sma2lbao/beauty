/**
 * 写工具确认卡:后端在 tool 帧里下发「需要人工确认」的写操作
 * (error_code=tool.confirmation_required + confirmation_id / expires_at),
 * 这里就地给出「确认执行 / 取消」,并承接三种收尾:
 * - 确认成功:展示执行结果(result_text 可折叠)并刷新会话消息;
 * - 取消成功:提示已取消,操作不会执行;
 * - 404/409(不存在、已过期、已处理):提示原因并刷新会话,不再允许重复操作。
 *
 * 状态机:idle → confirming / cancelling → confirmed / cancelled / resolved。
 * 请求期间两个按钮都禁用,防止重复点击触发第二次确认。
 */
import { useQueryClient } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';
import { useState } from 'react';

import { ConsoleButton } from '@/components/console/ui';
import {
  conversationsApi,
  type ToolConfirmationResult,
} from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { summarizeArguments, toneClasses } from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

type Phase =
  | { kind: 'idle' }
  | { kind: 'confirming' }
  | { kind: 'cancelling' }
  | { kind: 'confirmed'; result: ToolConfirmationResult }
  | { kind: 'cancelled' }
  | { kind: 'resolved'; message: string };

/** 收尾状态通知父卡片,用于把标题栏的「待确认」换成最终结果。 */
export type ToolConfirmationStatus =
  | 'confirmed'
  | 'failed'
  | 'cancelled'
  | 'resolved';

/** 409/404 表示这张确认已经失效:给出原因并刷新会话,不再保留按钮。 */
function resolvedMessage(failure: ApiError): string {
  switch (failure.code) {
    case 'tool_confirmation.expired':
      return '确认已过期,请重新发起该操作。';
    case 'tool_confirmation.not_pending':
      return '该操作已处理,会话已刷新。';
    case 'tool_confirmation.not_found':
      return '确认记录不存在或已处理,会话已刷新。';
    default:
      return failure.message || '该确认已失效,会话已刷新。';
  }
}

/** 过期时间只展示到分钟:用户关心「还能等多久」,不关心秒。 */
function formatExpiresAt(value: string | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const time = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
  return `确认有效期至 ${time}`;
}

export function ToolConfirmationCard({
  conversationId,
  confirmationId,
  argumentsText,
  expiresAt,
  onSettled,
}: {
  conversationId: string;
  confirmationId: string;
  /** 工具参数 JSON;摘要成一行展示,原始参数挂在 title 上。 */
  argumentsText: string;
  expiresAt?: string;
  onSettled?: (status: ToolConfirmationStatus) => void;
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [error, setError] = useState<string | null>(null);

  const refreshConversation = () =>
    Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.conversationMessages(conversationId),
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.conversation(conversationId),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
    ]);

  const settle = (status: ToolConfirmationStatus) => {
    onSettled?.(status);
  };

  const handleStale = (failure: ApiError) => {
    setPhase({ kind: 'resolved', message: resolvedMessage(failure) });
    settle('resolved');
    void refreshConversation();
  };

  const confirm = async () => {
    if (phase.kind !== 'idle') return;
    setPhase({ kind: 'confirming' });
    setError(null);
    try {
      const result = await conversationsApi.confirmToolCall(
        conversationId,
        confirmationId,
      );
      setPhase({ kind: 'confirmed', result });
      settle(result.ok === false ? 'failed' : 'confirmed');
      void refreshConversation();
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        (failure.status === 409 || failure.status === 404)
      ) {
        handleStale(failure);
        return;
      }
      setError(failure instanceof Error ? failure.message : '确认失败,请重试');
      setPhase({ kind: 'idle' });
    }
  };

  const cancel = async () => {
    if (phase.kind !== 'idle') return;
    setPhase({ kind: 'cancelling' });
    setError(null);
    try {
      await conversationsApi.cancelToolCall(conversationId, confirmationId);
      setPhase({ kind: 'cancelled' });
      settle('cancelled');
      void refreshConversation();
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        (failure.status === 409 || failure.status === 404)
      ) {
        handleStale(failure);
        return;
      }
      setError(failure instanceof Error ? failure.message : '取消失败,请重试');
      setPhase({ kind: 'idle' });
    }
  };

  const busy = phase.kind === 'confirming' || phase.kind === 'cancelling';
  const actionable = phase.kind === 'idle';
  const expiresLabel = formatExpiresAt(expiresAt);

  return (
    <div
      role="note"
      aria-label="工具写操作确认"
      className={cn('border-t px-3 py-2.5', toneClasses('warning').outline)}
    >
      <div className="flex items-start gap-2">
        <ShieldAlert
          size={14}
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-ha-warning"
        />
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-medium text-ha-warning">
            该操作会写入外部服务,确认后才会执行
          </p>
          {argumentsText ? (
            <p
              title={argumentsText}
              className="mt-1 truncate rounded-[3px] bg-ha-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-ha-muted"
            >
              {summarizeArguments(argumentsText)}
            </p>
          ) : null}
          {expiresLabel ? (
            <p className="mt-1 font-mono text-[10px] text-ha-faint">
              {expiresLabel}
            </p>
          ) : null}
        </div>
      </div>

      {actionable || busy ? (
        <div className="mt-2 flex items-center gap-2 pl-6">
          <ConsoleButton
            variant="primary"
            size="sm"
            disabled={!actionable}
            onClick={() => void confirm()}
          >
            {phase.kind === 'confirming' ? '确认中…' : '确认执行'}
          </ConsoleButton>
          <ConsoleButton
            variant="secondary"
            size="sm"
            disabled={!actionable}
            onClick={() => void cancel()}
          >
            {phase.kind === 'cancelling' ? '取消中…' : '取消'}
          </ConsoleButton>
        </div>
      ) : null}

      {error ? (
        <p className="mt-2 pl-6 text-[11px] leading-4 text-ha-danger">
          {error}
        </p>
      ) : null}

      {phase.kind === 'confirmed' ? (
        <ConfirmedResult result={phase.result} />
      ) : null}

      {phase.kind === 'cancelled' ? (
        <p className="mt-2 pl-6 text-[12px] text-ha-dim">
          已取消,该操作未执行。
        </p>
      ) : null}

      {phase.kind === 'resolved' ? (
        <p className="mt-2 pl-6 text-[12px] text-ha-warning">{phase.message}</p>
      ) : null}
    </div>
  );
}

/** 执行结果:成功/失败都可能有 result_text,默认展开、可折叠。 */
function ConfirmedResult({ result }: { result: ToolConfirmationResult }) {
  const failed = result.ok === false;
  return (
    <div className="mt-2 flex flex-col gap-1.5 pl-6">
      <p
        className={cn(
          'text-[12px] font-medium',
          failed ? 'text-ha-danger' : 'text-ha-success',
        )}
      >
        {failed ? '执行失败' : '执行完成'}
        {result.error_code ? (
          <span className="ml-2 font-mono text-[11px]">
            {result.error_code}
          </span>
        ) : null}
      </p>
      {result.result_text ? (
        <details
          open
          className="rounded-[3px] border border-ha-border bg-ha-panel-2"
        >
          <summary className="cursor-pointer px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
            执行结果
          </summary>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2 pb-2 font-mono text-[12px] leading-[20px] text-ha-muted">
            {result.result_text}
          </pre>
        </details>
      ) : null}
      {result.message_id ? (
        <p className="font-mono text-[10px] text-ha-faint">已写入会话消息</p>
      ) : null}
    </div>
  );
}
