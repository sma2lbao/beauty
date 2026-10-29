/**
 * 流式发送期间的临时气泡:用户消息 + 会话内只读工具调用流水 +
 * Agent 正在产出的正文(还没有正文时给占位文案)。
 */
import { StatusDot } from '@/components/console/ui';
import { formatDuration } from '@/lib/presentation';

import type { ChatPending } from './use-chat-stream';
import { AgentBlock, UserBlock } from './message-bubbles';

export function ChatPendingBlock({ pending }: { pending: ChatPending }) {
  return (
    <>
      <UserBlock
        text={pending.user}
        attachments={pending.attachments}
        mcpServers={pending.mcpServers}
      />
      {pending.chatTools.length > 0 ? (
        <ol className="space-y-1.5 border-l border-ha-border pl-3">
          {pending.chatTools.map((call, index) => {
            const duration = formatDuration(call.durationMs);
            let label = call.ok
              ? '调用工具 ' + call.name
              : '工具 ' + call.name + ' 被拒绝(会话中只允许只读操作)';
            if (!call.ok && call.errorCode) {
              label = '工具 ' + call.name + ' 失败(' + call.errorCode + ')';
            }
            return (
              <li
                key={call.name + '-' + index}
                className="flex items-center gap-2 text-[12px] text-ha-dim"
              >
                <StatusDot
                  tone={call.ok ? 'success' : 'danger'}
                  pulse={
                    index === pending.chatTools.length - 1 && !pending.reply
                  }
                />
                <span className="min-w-0 truncate">
                  {label}
                  {duration ? ' · ' + duration : ''}
                </span>
              </li>
            );
          })}
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
  );
}
