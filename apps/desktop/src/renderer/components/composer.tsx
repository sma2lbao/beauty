/**
 * 消息输入框:回车发送、Shift + 回车换行,运行中显示「停止」。
 *
 * 运行中禁用发送(本机 Agent 一轮内不接受追加指令,避免与主进程语义不符),
 * 停止走 agent.cancel,由主进程推送 run.finished 收敛运行态。
 */
import { ArrowUp, Square } from 'lucide-react';
import { useState } from 'react';

import { ConsoleButton } from '@/components/ui';

export function Composer({
  running,
  onSend,
  onCancel,
}: {
  running: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState('');
  const canSend = draft.trim().length > 0 && !running;

  const submit = () => {
    if (!canSend) return;
    const text = draft.trim();
    setDraft('');
    onSend(text);
  };

  return (
    <div className="shrink-0 px-4 pb-4 pt-2 sm:px-8">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="mx-auto w-full max-w-[820px] rounded-[10px] border border-ha-border-strong bg-ha-panel p-3 transition-colors focus-within:border-ha-accent/70"
      >
        <label className="sr-only" htmlFor="desktop-chat-input">
          输入消息
        </label>
        <textarea
          id="desktop-chat-input"
          aria-label="输入消息"
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
            running
              ? 'Agent 正在执行,停止后可继续发送…'
              : '描述要完成的事,或直接提问…'
          }
          className="block w-full resize-none bg-transparent px-1 py-0.5 text-[13px] leading-6 text-ha-text-body outline-none placeholder:text-ha-dim"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="min-w-0 truncate font-mono text-[11px] text-ha-faint">
            {running
              ? '运行中 · Enter 暂不可用'
              : 'Enter 发送 · Shift + Enter 换行'}
          </span>
          <div className="flex shrink-0 items-center gap-2">
            {running ? (
              <ConsoleButton variant="danger" size="sm" onClick={onCancel}>
                <Square aria-hidden="true" className="h-3 w-3" />
                停止
              </ConsoleButton>
            ) : null}
            <button
              type="submit"
              aria-label="发送"
              title="发送"
              disabled={!canSend}
              className="grid h-7 w-7 place-items-center rounded-md bg-ha-accent text-white transition-colors hover:bg-ha-accent-soft disabled:opacity-40"
            >
              <ArrowUp aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
