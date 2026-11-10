/**
 * 消息操作行(interaction-closure §2.2):复制 / 编辑重发 / 重新生成。
 * 桌面 hover 才浮出,触摸端常显;复制失败与流内错误就地提示,不弹全局错误条。
 */
import { useState } from 'react';

import { ConsoleButton } from '@/components/console/ui';

type CopyState = 'idle' | 'copied' | 'failed';

/** 写剪贴板:非安全上下文/jsdom 可能没有 clipboard,失败由调用方就地提示。 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function ActionButton({
  children,
  disabled,
  onClick,
}: {
  children: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded px-1.5 py-0.5 font-mono text-[11px] text-ha-dim transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ha-dim"
    >
      {children}
    </button>
  );
}

export function MessageActionRow({
  text,
  busy,
  error,
  onEditResend,
  onRegenerate,
}: {
  text: string;
  /** 流式进行中:禁用全部消息操作,避免并发改写同一轮。 */
  busy: boolean;
  /** 该消息就地错误(编辑重发/重新生成失败的中文文案)。 */
  error?: string | null;
  onEditResend?: () => void;
  onRegenerate?: () => void;
}) {
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const handleCopy = async () => {
    setCopyState((await copyText(text)) ? 'copied' : 'failed');
  };
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      <div className="ml-auto flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
        {copyState === 'copied' ? (
          <span className="px-1 font-mono text-[11px] text-ha-muted">
            已复制
          </span>
        ) : null}
        {copyState === 'failed' ? (
          <span role="status" className="px-1 font-mono text-[11px] text-ha-danger">
            复制失败
          </span>
        ) : null}
        <ActionButton disabled={busy} onClick={() => void handleCopy()}>
          复制
        </ActionButton>
        {onEditResend ? (
          <ActionButton disabled={busy} onClick={onEditResend}>
            编辑重发
          </ActionButton>
        ) : null}
        {onRegenerate ? (
          <ActionButton disabled={busy} onClick={onRegenerate}>
            重新生成
          </ActionButton>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-right font-mono text-[11px] text-ha-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export const EDIT_MESSAGE_MAX = 2000;

/** 编辑重发的就地输入框:1..2000 字,冲突/额度等错误由外层传回。 */
export function EditMessageForm({
  initial,
  busy,
  error,
  onCancel,
  onSubmit,
}: {
  initial: string;
  busy: boolean;
  error?: string | null;
  onCancel: () => void;
  onSubmit: (content: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [localError, setLocalError] = useState<string | null>(null);
  const submit = () => {
    const content = draft.trim();
    if (!content || content.length > EDIT_MESSAGE_MAX) {
      setLocalError('内容长度需为 1–2000 字。');
      return;
    }
    setLocalError(null);
    onSubmit(content);
  };
  return (
    <div className="mt-2 flex flex-col gap-2">
      <textarea
        aria-label="编辑消息内容"
        value={draft}
        rows={3}
        onChange={(event) => {
          setDraft(event.target.value);
          setLocalError(null);
        }}
        className="w-full resize-y rounded-md border border-ha-border-strong bg-ha-panel px-3 py-2 text-[14px] leading-[23px] text-ha-text outline-none focus:border-ha-accent"
      />
      <div className="flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto font-mono text-[11px] text-ha-faint">
          保存后从这条消息重新生成回复 · {draft.trim().length}/2000
        </span>
        <ConsoleButton variant="secondary" size="sm" disabled={busy} onClick={onCancel}>
          取消
        </ConsoleButton>
        <ConsoleButton variant="primary" size="sm" disabled={busy} onClick={submit}>
          确认重发
        </ConsoleButton>
      </div>
      {localError ?? error ? (
        <p role="alert" className="font-mono text-[11px] text-ha-danger">
          {localError ?? error}
        </p>
      ) : null}
    </div>
  );
}
