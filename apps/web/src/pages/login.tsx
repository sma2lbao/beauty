/**
 * 登录页(架构 §10):`token`/`logto` 模式下整站的登录门,守卫(auth-guard)
 * 拦下的访问都回到这里,登录成功后送回来路。
 *
 * - `token`:自建认证(account-service)——邮箱 + 密码登录、注册新组织,
 *   或粘贴现成 access token(联调/运维,无续期);登录拿双令牌,
 *   access 过期由 http 层用 refresh 自动续;
 * - `logto`:跳 IdP 授权码 + PKCE 登录,回调在 `/callback`。
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

import { Button, Input } from '@beauty/design';

import { BrandMark, BrandWordmark } from '@/components/brand/logo';
import { accountApi } from '@/lib/account-api';
import {
  accountLabel,
  authMode,
  rememberLoginRedirect,
  setStoredToken,
  setStoredTokens,
  signIn,
  signOut,
  takeLoginRedirect,
} from '@/lib/auth';

const ACCOUNT_QUERY_KEY = ['auth-account'];

const FIELD_CLASS = 'mt-1';
const LABEL_CLASS = 'block text-sm font-medium';

function SignedIn({
  account,
  busy,
  onEnter,
  onLeave,
}: {
  account: string;
  busy: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  return (
    <>
      <p className="mt-4 text-sm text-muted-foreground">已登录:{account}</p>
      <Button className="mt-5 w-full" onClick={onEnter}>
        进入控制台
      </Button>
      <Button
        variant="outline"
        className="mt-3 w-full"
        disabled={busy}
        onClick={onLeave}
      >
        退出登录
      </Button>
    </>
  );
}

export function LoginPage() {
  const mode = authMode();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  // 守卫带来的来路(站内路径,防开放重定向);没有来路回对话页
  const from = (location.state as { from?: { pathname?: string } } | null)
    ?.from?.pathname;
  const target =
    from?.startsWith('/') && !from.startsWith('//') ? from : '/';

  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountQuery = useQuery({
    queryKey: ACCOUNT_QUERY_KEY,
    queryFn: accountLabel,
  });
  const account = accountQuery.data ?? null;

  const enter = () => navigate(target, { replace: true });

  async function attempt(task: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await task();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败');
      return false;
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = () =>
    void attempt(async () => {
      if (tab === 'login') {
        const result = await accountApi.login({
          email: email.trim(),
          password,
        });
        setStoredTokens(result.access_token, result.refresh_token, result.subject);
      } else {
        const result = await accountApi.register({
          email: email.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
        setStoredTokens(result.access_token, result.refresh_token, result.subject);
      }
      await queryClient.invalidateQueries({ queryKey: ACCOUNT_QUERY_KEY });
    }).then((ok) => {
      if (ok) {
        enter();
      }
    });

  const loginWithToken = () =>
    void attempt(async () => {
      setStoredToken(token.trim());
      await queryClient.invalidateQueries({ queryKey: ACCOUNT_QUERY_KEY });
    }).then((ok) => {
      if (ok) {
        enter();
      }
    });

  const goLogto = () =>
    void attempt(async () => {
      // IdP 登录是整页跳转,来路先记下;真跳转时不会走到后面的 navigate
      rememberLoginRedirect(target);
      await signIn();
      navigate(takeLoginRedirect(), { replace: true });
    });

  const leave = () =>
    void attempt(async () => {
      await signOut();
      await queryClient.invalidateQueries({ queryKey: ACCOUNT_QUERY_KEY });
    });

  return (
    <main className="flex min-h-svh items-center justify-center bg-background px-6 py-12 text-foreground">
      <div className="w-full max-w-sm">
        <div
          className="overflow-hidden rounded-xl border bg-card shadow-sm"
          data-testid="login-card"
        >
          {/*
            夜空条:官方 favicon 的夜空几何(径向渐变同源),月面半沉在天际线
            上构成"月出" —— 登录页唯一的一次设计动作,表单区保持安静。
          */}
          <div className="relative h-28 shrink-0 bg-[radial-gradient(72%_72%_at_50%_38%,#331E6E_0%,#150E31_100%)]">
            <BrandWordmark className="absolute left-5 top-4 text-lg text-[#FFEFC6]" />
            <BrandMark decorative className="absolute -bottom-8 right-6 w-20" />
          </div>

          <div className="p-6 pt-12">
            <h1 className="font-display text-2xl">登录</h1>

            {mode === 'token' && account ? (
              <SignedIn
                account={account}
                busy={busy}
                onEnter={enter}
                onLeave={leave}
              />
            ) : null}

            {mode === 'token' && !account ? (
              <>
                <p className="mt-2 text-sm text-muted-foreground">
                  {tab === 'login'
                    ? '用邮箱和密码登录;还没有账号就注册一个,首次注册会建立你的组织。'
                    : '注册即创建你的组织,你就是组织所有者(owner)。'}
                </p>

                <div className="mt-4 flex gap-1 rounded-lg bg-muted p-1 text-sm" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'login'}
                    className={
                      'flex-1 rounded-md px-3 py-1.5 transition-colors ' +
                      (tab === 'login'
                        ? 'bg-background font-medium shadow-sm'
                        : 'text-muted-foreground')
                    }
                    onClick={() => setTab('login')}
                  >
                    登录
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === 'register'}
                    className={
                      'flex-1 rounded-md px-3 py-1.5 transition-colors ' +
                      (tab === 'register'
                        ? 'bg-background font-medium shadow-sm'
                        : 'text-muted-foreground')
                    }
                    onClick={() => setTab('register')}
                  >
                    注册
                  </button>
                </div>

                <form
                  className="mt-4 space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitPassword();
                  }}
                >
                  <label className={LABEL_CLASS}>
                    邮箱
                    <Input
                      type="email"
                      required
                      className={FIELD_CLASS}
                      placeholder="you@example.com"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  </label>
                  <label className={LABEL_CLASS}>
                    密码
                    <Input
                      type="password"
                      required
                      minLength={8}
                      className={FIELD_CLASS}
                      placeholder={tab === 'login' ? '输入密码' : '至少 8 位'}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                  </label>
                  {tab === 'register' ? (
                    <label className={LABEL_CLASS}>
                      显示名(可选)
                      <Input
                        className={FIELD_CLASS}
                        placeholder="怎么称呼你"
                        value={displayName}
                        onChange={(event) => setDisplayName(event.target.value)}
                      />
                    </label>
                  ) : null}
                  <Button
                    type="submit"
                    className="w-full"
                    disabled={busy || !email.trim() || !password}
                  >
                    {tab === 'login' ? '登录' : '创建账号'}
                  </Button>
                </form>

                <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="h-px flex-1 bg-border" aria-hidden="true" />
                  或
                  <span className="h-px flex-1 bg-border" aria-hidden="true" />
                </div>

                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    loginWithToken();
                  }}
                >
                  <label className={LABEL_CLASS}>
                    access token
                    <Input
                      type="password"
                      className={FIELD_CLASS}
                      placeholder="粘贴令牌(联调/运维用,不自动续期)"
                      value={token}
                      onChange={(event) => setToken(event.target.value)}
                    />
                  </label>
                  <Button
                    type="submit"
                    variant="outline"
                    className="mt-3 w-full"
                    disabled={busy || !token.trim()}
                  >
                    用令牌进入
                  </Button>
                </form>
              </>
            ) : null}

            {mode === 'logto' ? (
              account ? (
                <SignedIn
                  account={account}
                  busy={busy}
                  onEnter={enter}
                  onLeave={leave}
                />
              ) : (
                <>
                  <p className="mt-2 text-sm text-muted-foreground">
                    跳转到 Logto 完成登录,回来后接着你刚才的页面。
                  </p>
                  <Button
                    className="mt-5 w-full"
                    size="lg"
                    disabled={busy}
                    onClick={goLogto}
                  >
                    使用 Logto 登录
                  </Button>
                </>
              )
            ) : null}

            {error ? (
              <p className="mt-4 text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </main>
  );
}
