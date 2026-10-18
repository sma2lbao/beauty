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
import { useLocation, useNavigate, useSearchParams } from 'react-router';

import { Button, Field, Input, Tabs } from '@beauty/design';

import { BrandLockup } from '@/components/brand/logo';
import { accountApi } from '@/lib/account-api';
import { siteUrl } from '@/lib/site-links';
import {
  accountLabel,
  authMode,
  oauthStartUrl,
  rememberLoginRedirect,
  sessionLogin,
  sessionRegister,
  setStoredToken,
  setStoredTokens,
  signIn,
  signOut,
  takeLoginRedirect,
} from '@/lib/auth';

const ACCOUNT_QUERY_KEY = ['auth-account'];

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
  const [searchParams] = useSearchParams();
  const oauthError = searchParams.get('oauth_error');
  const queryClient = useQueryClient();
  // 守卫带来的来路(站内路径,防开放重定向);没有来路回对话页
  const from = (location.state as { from?: { pathname?: string } } | null)?.from
    ?.pathname;
  const target = from?.startsWith('/') && !from.startsWith('//') ? from : '/';

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
      if (mode === 'session') {
        if (tab === 'login') {
          await sessionLogin(email.trim(), password);
        } else {
          await sessionRegister({
            email: email.trim(),
            password,
            display_name: displayName.trim() || undefined,
          });
        }
      } else if (tab === 'login') {
        const result = await accountApi.login({
          email: email.trim(),
          password,
        });
        setStoredTokens(
          result.access_token,
          result.refresh_token,
          result.subject,
        );
      } else {
        const result = await accountApi.register({
          email: email.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
        setStoredTokens(
          result.access_token,
          result.refresh_token,
          result.subject,
        );
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
    <main className="flex min-h-svh flex-col items-center justify-center bg-ha-bg px-6 py-12 text-ha-text-body">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <a href={siteUrl('/')} aria-label="beauty moon 官网首页">
            <BrandLockup size={34} textClassName="text-[20px]" />
          </a>
          <p className="text-[12px] leading-5 text-ha-muted">
            把任务交给 Agent,过程与产物都在掌握里
          </p>
        </div>

        <div
          className="rounded-xl border border-ha-border bg-ha-sidebar shadow-[0_24px_80px_rgba(0,0,0,0.45)]"
          data-testid="login-card"
        >
          <div className="p-6">
            <h1 className="text-[18px] font-semibold text-ha-text">登录</h1>

            {(mode === 'token' || mode === 'session') && account ? (
              <SignedIn
                account={account}
                busy={busy}
                onEnter={enter}
                onLeave={leave}
              />
            ) : null}

            {(mode === 'token' || mode === 'session') && !account ? (
              <>
                <p className="mt-2 text-sm text-muted-foreground">
                  {tab === 'login'
                    ? '用邮箱和密码登录;还没有账号就注册一个,首次注册会建立你的组织。'
                    : '注册即创建你的组织,你就是组织所有者(owner)。'}
                </p>

                <Tabs
                  className="mt-4"
                  block
                  items={[
                    { value: 'login', label: '登录' },
                    { value: 'register', label: '注册' },
                  ]}
                  value={tab}
                  onValueChange={(value) =>
                    setTab(value as 'login' | 'register')
                  }
                />

                <form
                  className="mt-4 space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitPassword();
                  }}
                >
                  <Field label="邮箱" htmlFor="login-email">
                    <Input
                      id="login-email"
                      type="email"
                      required
                      placeholder="you@example.com"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  </Field>
                  <Field label="密码" htmlFor="login-password">
                    <Input
                      id="login-password"
                      type="password"
                      required
                      minLength={8}
                      placeholder={tab === 'login' ? '输入密码' : '至少 8 位'}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                  </Field>
                  {tab === 'register' ? (
                    <Field label="显示名(可选)" htmlFor="login-display-name">
                      <Input
                        id="login-display-name"
                        placeholder="怎么称呼你"
                        value={displayName}
                        onChange={(event) => setDisplayName(event.target.value)}
                      />
                    </Field>
                  ) : null}
                  <Button
                    type="submit"
                    className="w-full"
                    disabled={busy || !email.trim() || !password}
                  >
                    {tab === 'login' ? '登录' : '创建账号'}
                  </Button>
                </form>

                {mode === 'token' ? (
                  <>
                    <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
                      <span
                        className="h-px flex-1 bg-border"
                        aria-hidden="true"
                      />
                      或
                      <span
                        className="h-px flex-1 bg-border"
                        aria-hidden="true"
                      />
                    </div>

                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        loginWithToken();
                      }}
                    >
                      <Field label="access token" htmlFor="login-token">
                        <Input
                          id="login-token"
                          type="password"
                          placeholder="粘贴令牌(联调/运维用,不自动续期)"
                          value={token}
                          onChange={(event) => setToken(event.target.value)}
                        />
                      </Field>
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

                {mode === 'session' ? (
                  <div className="mt-5 grid gap-2">
                    <a
                      href={oauthStartUrl('google')}
                      className="inline-flex h-9 items-center justify-center rounded-md border border-ha-border-strong bg-ha-panel-2 text-[13px] font-medium text-ha-text-2 transition-colors hover:text-ha-text"
                    >
                      使用 Google 登录
                    </a>
                    <a
                      href={oauthStartUrl('github')}
                      className="inline-flex h-9 items-center justify-center rounded-md border border-ha-border-strong bg-ha-panel-2 text-[13px] font-medium text-ha-text-2 transition-colors hover:text-ha-text"
                    >
                      使用 GitHub 登录
                    </a>
                  </div>
                ) : null}
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

            {oauthError ? (
              <p className="mt-4 text-sm text-destructive" role="alert">
                社交登录未完成({oauthError})
              </p>
            ) : null}

            {error ? (
              <p className="mt-4 text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
          </div>
        </div>
        <p className="mt-5 text-center font-mono text-[11px] text-ha-faint">
          <a
            href={siteUrl('/')}
            className="underline-offset-4 hover:text-ha-muted hover:underline"
          >
            返回产品官网
          </a>
        </p>
      </div>
    </main>
  );
}
