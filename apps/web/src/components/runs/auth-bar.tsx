import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Button, Input } from '@beauty/design';
import {
  accountLabel,
  authMode,
  setStoredToken,
  signIn,
  signOut,
} from '@/lib/auth';
import { queryKeys } from '@/lib/query-keys';

const MODE_LABEL: Record<string, string> = {
  token: '账号登录',
  logto: 'Logto 登录',
};

/**
 * 鉴权面板:当前身份 + 登出(§10 的浏览器侧入口)。
 * 登录本身在 `/login` 页(密码/注册);这里保留粘贴 access token 的
 * 联调入口与登录页共用一套存储。
 */
export function AuthBar({ onChange }: { onChange?: () => void }) {
  const queryClient = useQueryClient();
  const mode = authMode();
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountQuery = useQuery({
    queryKey: queryKeys.authAccount,
    queryFn: accountLabel,
  });
  const account = accountQuery.data ?? null;

  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await task();
      await queryClient.invalidateQueries({ queryKey: queryKeys.authAccount });
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
      </span>

      {mode === 'token' ? (
        account ? (
          <>
            <span>已登录:{account}</span>
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
          <span className="text-xs text-muted-foreground">
            未登录:登录请访问 <code className="text-foreground">/login</code>
          </span>
        )
      ) : null}

      {mode === 'token' && !account ? (
        <>
          <span className="text-xs text-muted-foreground">
            或粘贴令牌(联调):
          </span>
          <Input
            aria-label="access token"
            type="password"
            placeholder="粘贴 access token"
            className="min-w-48 flex-1"
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
