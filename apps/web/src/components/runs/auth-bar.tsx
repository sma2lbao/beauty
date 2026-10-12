import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  accountLabel,
  authMode,
  getStoredToken,
  setStoredToken,
  signIn,
  signOut,
  tenantId,
} from '@/lib/auth';

const MODE_LABEL: Record<string, string> = {
  dev: '开发模式',
  token: '手动令牌',
  logto: 'Logto 登录',
};

const ACCOUNT_QUERY_KEY = ['auth-account'];

/** 鉴权面板:按模式展示当前身份与可用的登录方式(§10 的浏览器侧入口)。 */
export function AuthBar({ onChange }: { onChange?: () => void }) {
  const queryClient = useQueryClient();
  const mode = authMode();
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountQuery = useQuery({
    queryKey: ACCOUNT_QUERY_KEY,
    queryFn: accountLabel,
  });
  const account = accountQuery.data ?? null;

  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await task();
      await queryClient.invalidateQueries({ queryKey: ACCOUNT_QUERY_KEY });
      onChange?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-xl border p-4 text-sm"
      data-testid="auth-bar"
    >
      <span className="text-muted-foreground">
        鉴权:{MODE_LABEL[mode] ?? mode}
        {mode === 'dev' ? ` · 租户 ${tenantId()}` : ''}
      </span>

      {mode === 'dev' ? (
        <span className="text-xs text-muted-foreground">
          后端 AUTH_ENABLED=false 时用 X-Tenant-Id 临时头
        </span>
      ) : null}

      {mode === 'token' ? (
        <>
          <input
            aria-label="access token"
            type="password"
            placeholder="粘贴 IdP access token"
            className="min-w-64 flex-1 rounded-md border bg-transparent px-3 py-2 text-sm"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
          <Button
            size="sm"
            disabled={busy || !token.trim()}
            onClick={() =>
              run(async () => {
                setStoredToken(token.trim());
                setToken('');
              })
            }
          >
            保存令牌
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !getStoredToken()}
            onClick={() =>
              run(async () => {
                setStoredToken(null);
              })
            }
          >
            清除
          </Button>
          {account ? (
            <span className="text-xs text-muted-foreground">已设置</span>
          ) : null}
        </>
      ) : null}

      {mode === 'logto' ? (
        account ? (
          <>
            <span>{account}</span>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => run(signOut)}
            >
              退出登录
            </Button>
          </>
        ) : (
          <Button size="sm" disabled={busy} onClick={() => run(signIn)}>
            登录
          </Button>
        )
      ) : null}

      {error ? <span className="text-destructive">{error}</span> : null}
    </div>
  );
}
