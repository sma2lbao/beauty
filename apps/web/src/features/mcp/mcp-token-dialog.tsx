/**
 * 改用静态令牌弹窗:浏览器 OAuth 走不通时(server 只支持静态令牌、
 * 元数据缺失、不支持动态注册…),直接把访问令牌 PATCH 到 server。
 *
 * 安全边界:令牌只随这一次提交发给后端,不做本地缓存;提交/关闭即清空。
 */
import { useId, useState } from 'react';

import { ConsoleDialog } from '@/components/console/dialog';
import { ConsoleButton } from '@/components/console/ui';

export type McpTokenDialogProps = {
  open: boolean;
  /** server 名,用于标题与说明 */
  serverName: string;
  pending: boolean;
  error: string | null;
  /** 提交新令牌;接口约定空串表示清除,这里只提交非空输入 */
  onSubmit: (token: string) => void;
  onClose: () => void;
};

const INPUT_CLASS =
  'w-full rounded-md border border-ha-border-strong bg-ha-code px-2.5 py-2 font-mono text-[13px] text-ha-text outline-none focus:border-ha-accent-soft';

export function McpTokenDialog({
  open,
  serverName,
  pending,
  error,
  onSubmit,
  onClose,
}: McpTokenDialogProps) {
  const [token, setToken] = useState('');
  const tokenId = useId();

  const close = () => {
    setToken('');
    onClose();
  };

  const trimmed = token.trim();
  const canSubmit = trimmed.length > 0 && !pending;

  const submit = () => {
    if (!canSubmit) return;
    onSubmit(trimmed);
    setToken('');
  };

  return (
    <ConsoleDialog
      open={open}
      title={'改用令牌连接 ' + serverName}
      description="保存后该 server 会立即使用这个静态令牌访问,不再走浏览器授权。"
      onClose={close}
      footer={
        <>
          <ConsoleButton
            variant="ghost"
            size="sm"
            disabled={pending}
            onClick={close}
          >
            取消
          </ConsoleButton>
          <ConsoleButton
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            onClick={submit}
          >
            {pending ? '保存中…' : '保存令牌'}
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
        <div>
          <label
            htmlFor={tokenId}
            className="mb-1 block text-xs font-medium text-ha-text-2"
          >
            访问令牌
          </label>
          <input
            id={tokenId}
            autoFocus
            required
            type="password"
            spellCheck={false}
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴该 server 的访问令牌"
            className={INPUT_CLASS}
          />
        </div>
        <p className="text-[11px] leading-5 text-ha-muted">
          令牌只在服务端加密保存,提交后输入框会清空,页面不会回显。
        </p>
        {error ? (
          <p role="alert" className="text-[11px] leading-5 text-ha-danger">
            {error}
          </p>
        ) : null}
      </form>
    </ConsoleDialog>
  );
}
