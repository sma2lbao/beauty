/**
 * 工具调用卡片:会话里 role=tool 的消息。
 *
 * 契约里工具消息只有 toolName / toolStatus / content,所以卡片只表达三件事:
 * 工具名、执行状态(运行中/成功/失败)、可折叠的输出正文。
 * 与 apps/web 的 ToolCallCard 同一套排版手法。
 */
import { ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import { useState } from 'react';

import type { ChatMessage } from '@shared/contract';

import { cn } from '@/lib/utils';

import type { Tone } from '@/components/ui';
import { StatusDot, StatusText } from '@/components/ui';

const STATUS_META: Record<
  NonNullable<ChatMessage['toolStatus']>,
  { tone: Tone; label: string }
> = {
  running: { tone: 'info', label: '运行中' },
  ok: { tone: 'success', label: '成功' },
  error: { tone: 'danger', label: '失败' },
};

const BORDER: Record<Tone, string> = {
  neutral: 'border-ha-border',
  info: 'border-ha-info-border',
  success: 'border-ha-border',
  warning: 'border-ha-warning-border',
  danger: 'border-ha-danger-border',
};

/** 折叠态展示输出首行,像终端里的命令摘要。 */
function firstLine(content: string): string {
  const line = content.split('\n').find((item) => item.trim().length > 0);
  return line?.trim() ?? '无输出';
}

export function ToolMessageCard({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS_META[message.toolStatus ?? 'running'];
  const output = message.content.trim();

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border bg-ha-panel',
        BORDER[meta.tone],
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-ha-panel-2/50"
      >
        {open ? (
          <ChevronDown
            aria-hidden="true"
            className="h-3 w-3 shrink-0 text-ha-dim"
          />
        ) : (
          <ChevronRight
            aria-hidden="true"
            className="h-3 w-3 shrink-0 text-ha-dim"
          />
        )}
        <Terminal
          aria-hidden="true"
          className={cn(
            'h-3.5 w-3.5 shrink-0',
            meta.tone === 'danger'
              ? 'text-ha-danger'
              : meta.tone === 'info'
                ? 'text-ha-info'
                : 'text-ha-muted',
          )}
        />
        <span className="shrink-0 font-mono text-[12px] font-medium text-ha-text-body">
          工具 · {message.toolName ?? '未命名工具'}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ha-dim">
          {firstLine(message.content)}
        </span>
        <StatusDot tone={meta.tone} pulse={message.toolStatus === 'running'} />
        <StatusText tone={meta.tone}>{meta.label}</StatusText>
      </button>

      {open ? (
        <div className="border-t border-ha-border bg-ha-code">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
              输出
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              {message.content.length} 字符
            </span>
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-[12px] leading-5 text-ha-muted">
            {output || '(无输出)'}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
