/**
 * 粘贴令牌连接弹窗:token_connectable 的 provider(如 Notion 内部集成)不需要
 * OAuth 应用凭证,粘贴集成令牌即可建立连接。
 *
 * 安全边界:令牌只随这一次提交发给后端,不做任何本地缓存;
 * 提交后立刻清空输入框,关闭弹窗也清空。
 */
import { useId, useState } from 'react';

import { ConsoleButton } from '@/components/console/ui';
import { ConsoleDialog } from '@/components/console/dialog';

export type ConnectorTokenSubmit = {
  token: string;
  display_name?: string;
};

export type ConnectorTokenDialogProps = {
  open: boolean;
  providerName: string;
  pending: boolean;
  error: string | null;
  onSubmit: (input: ConnectorTokenSubmit) => void;
  onClose: () => void;
};

const INPUT_CLASS =
  'w-full rounded-md border border-ha-border-strong bg-ha-code px-2.5 py-2 text-[13px] text-ha-text outline-none focus:border-ha-accent-soft';

export function ConnectorTokenDialog({
  open,
  providerName,
  pending,
  error,
  onSubmit,
  onClose,
}: ConnectorTokenDialogProps) {
  const [token, setToken] = useState('');
  const [displayName, setDisplayName] = useState('');
  const tokenId = useId();
  const displayNameId = useId();

  // 关闭即清空,避免令牌留在 React state 里(提交成功由模型关闭弹窗,
  // 提交时也已清空,所以这里只需覆盖手动取消/关闭)
  const close = () => {
    setToken('');
    setDisplayName('');
    onClose();
  };

  const trimmedToken = token.trim();
  const canSubmit = trimmedToken.length > 0 && !pending;

  const submit = () => {
    if (!canSubmit) return;
    const name = displayName.trim();
    onSubmit({ token: trimmedToken, ...(name ? { display_name: name } : {}) });
    // 令牌只发给后端这一次,不留在输入框里
    setToken('');
    setDisplayName('');
  };

  return (
    <ConsoleDialog
      open={open}
      title={`粘贴令牌连接 ${providerName}`}
      description="用内部集成令牌直接连接，不需要应用凭证。"
      onClose={close}
      footer={
        <>
          <ConsoleButton
            variant="ghost"
            size="sm"
            onClick={close}
            disabled={pending}
          >
            取消
          </ConsoleButton>
          <ConsoleButton
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            onClick={submit}
          >
            {pending ? '连接中…' : '连接'}
          </ConsoleButton>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="rounded-lg border border-ha-border bg-ha-panel-2 px-3 py-2 text-xs leading-5 text-ha-text-2">
          <p className="font-medium text-ha-text">令牌怎么获取</p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-4">
            <li>
              打开第三方的集成设置页（Notion：Settings → Connections → Develop
              or manage integrations）。
            </li>
            <li>新建 internal integration，复制它的 secret。</li>
            <li>在目标页面把该集成加进 Share，否则读不到内容。</li>
          </ol>
        </div>

        <div>
          <label
            htmlFor={tokenId}
            className="mb-1 block text-xs font-medium text-ha-text-2"
          >
            集成令牌
          </label>
          <textarea
            id={tokenId}
            autoFocus
            required
            rows={3}
            spellCheck={false}
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴 internal integration token"
            className={`${INPUT_CLASS} resize-y font-mono`}
          />
        </div>

        <div>
          <label
            htmlFor={displayNameId}
            className="mb-1 block text-xs font-medium text-ha-text-2"
          >
            显示名（可选）
          </label>
          <input
            id={displayNameId}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="例如：我的 Notion"
            className={INPUT_CLASS}
          />
        </div>

        {error ? (
          <p role="alert" className="text-[11px] leading-5 text-ha-danger">
            {error}
          </p>
        ) : null}
      </form>
    </ConsoleDialog>
  );
}
