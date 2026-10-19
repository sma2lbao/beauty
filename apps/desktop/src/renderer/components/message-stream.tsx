/**
 * 消息流:user / assistant / tool 三种角色的展示。
 *
 * - user 右侧气泡(短文本、可断词);
 * - assistant 左侧平铺正文,流式增量时在末尾显示光标;
 * - tool 交给 ToolMessageCard(可折叠输出)。
 */
import { Sparkles } from 'lucide-react';

import type { ChatMessage } from '@shared/contract';

import { clockTime } from '@/lib/format';

import { ToolMessageCard } from '@/components/tool-message-card';

export function MessageStream({
  messages,
  running,
}: {
  messages: ChatMessage[];
  running: boolean;
}) {
  return (
    <div className="flex flex-col gap-6">
      {messages.map((message, index) => {
        const streaming =
          running &&
          message.role === 'assistant' &&
          index === messages.length - 1;

        if (message.role === 'tool') {
          return <ToolMessageCard key={message.id} message={message} />;
        }

        if (message.role === 'user') {
          return (
            <article
              key={message.id}
              aria-label="用户消息"
              className="flex flex-col items-end gap-1.5"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-ha-faint">
                  {clockTime(message.createdAt)}
                </span>
                <span className="text-[11px] font-semibold text-ha-dim">
                  你
                </span>
              </div>
              <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg border border-ha-border bg-ha-panel-2 px-3.5 py-2.5 text-[13px] leading-6 text-ha-text-body">
                {message.content}
              </p>
            </article>
          );
        }

        return (
          <article
            key={message.id}
            aria-label="Agent 回复"
            className="flex flex-col gap-2"
          >
            <header className="flex items-center gap-2">
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-ha-panel-2">
                <Sparkles
                  aria-hidden="true"
                  className="h-3 w-3 text-ha-accent-soft"
                />
              </span>
              <span className="text-[12px] font-semibold text-ha-text">
                beauty moon Agent
              </span>
              <span className="font-mono text-[11px] text-ha-faint">
                {clockTime(message.createdAt)}
              </span>
            </header>
            <div className="whitespace-pre-wrap break-words text-[14px] leading-[23px] text-ha-text-body">
              {message.content}
              {streaming ? (
                <span
                  aria-hidden="true"
                  className="ml-0.5 inline-block h-[13px] w-[7px] translate-y-[2px] animate-pulse rounded-[1px] bg-ha-accent-soft"
                />
              ) : null}
              {streaming && !message.content ? (
                <span className="text-ha-dim">正在生成…</span>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
