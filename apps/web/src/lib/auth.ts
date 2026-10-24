/**
 * 前端鉴权接线(与后端 §10 的 token→租户约定配对)。
 *
 * 模式(`VITE_AUTH_MODE`):
 * - `token`(默认):自建认证登录(邮箱 + 密码,注册见登录页),双令牌存
 *   localStorage:access 过期后由 `@/lib/http` 拿 refresh 自动续(旋转式);
 *   运维仍可粘贴纯 access token(联调用,无续期能力);
 * - `logto`:Logto 授权码 + PKCE 登录,access token 由 SDK 自动续期。
 *
 * 租户标识只在 token 里(`tenant_id` claim),前端不传、也不需要知道它——
 * 守卫(auth-guard)保证未登录访问送回登录页,请求层不兜底临时租户头。
 *
 * 构建期环境变量:`VITE_AUTH_MODE`、`VITE_LOGTO_ENDPOINT`、`VITE_LOGTO_APP_ID`、
 * `VITE_LOGTO_RESOURCE`、`VITE_ACCOUNT_API_URL`(见 apps/web/.env.example)。
 */

import LogtoClient from '@logto/browser';

import {
  accountBase as ACCOUNT_BASE,
  authMode as MODE,
  logto as logtoEnv,
  type AuthMode,
} from '@/config/env';

export type { AuthMode };

const {
  endpoint: LOGTO_ENDPOINT,
  appId: LOGTO_APP_ID,
  resource: LOGTO_RESOURCE,
} = logtoEnv;
const TOKEN_KEY = 'beauty.auth.access_token';
const REFRESH_KEY = 'beauty.auth.refresh_token';
const LABEL_KEY = 'beauty.auth.account_label';

export function authMode(): AuthMode {
  return MODE;
}

/** session 模式:token 只在 httpOnly Cookie,BFF 同源 /api/auth/*。 */
function isSession(): boolean {
  return MODE === 'session';
}

function cookieValue(name: string): string | null {
  const match = document.cookie
    .split('; ')
    .find((item) => item.startsWith(name + '='));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

function csrfHeaders(): Record<string, string> {
  const token = cookieValue('bm_csrf');
  return token ? { 'X-CSRF-Token': token } : {};
}

async function sessionFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...csrfHeaders(),
      ...init.headers,
    },
  });
}

async function sessionResult(response: Response): Promise<void> {
  if (response.ok) return;
  let message = '登录失败';
  try {
    const body = (await response.json()) as { message?: string };
    message = body.message ?? message;
  } catch {
    // 非 JSON 错误体保留兜底文案
  }
  throw new Error(message);
}

export async function sessionLogin(
  email: string,
  password: string,
): Promise<void> {
  const response = await sessionFetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  await sessionResult(response);
}

export async function sessionRegister(input: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<void> {
  const response = await sessionFetch('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(input),
  });
  await sessionResult(response);
}

export function oauthStartUrl(provider: 'google' | 'github'): string {
  return '/api/auth/oauth/' + provider + '/start';
}

export function getStoredToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredRefreshToken(): string | null {
  return window.localStorage.getItem(REFRESH_KEY);
}

function clearStoredTokens(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
  window.localStorage.removeItem(LABEL_KEY);
}

/**
 * 登录/注册/刷新成功后保存双令牌(刷新时 label 传 undefined 即保留原值)。
 */
export function setStoredTokens(
  accessToken: string,
  refreshToken: string | null,
  label?: string,
): void {
  window.localStorage.setItem(TOKEN_KEY, accessToken);
  if (refreshToken) {
    window.localStorage.setItem(REFRESH_KEY, refreshToken);
  } else {
    window.localStorage.removeItem(REFRESH_KEY);
  }
  if (label !== undefined) {
    window.localStorage.setItem(LABEL_KEY, label);
  }
}

/**
 * 仅保存 access token(粘贴令牌/联调):无 refresh 即无续期,过期需重粘;
 * 会清掉旧 refresh 与账号标签,避免"新 access + 旧 refresh"错配。
 */
export function setStoredToken(token: string | null): void {
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.removeItem(REFRESH_KEY);
    window.localStorage.removeItem(LABEL_KEY);
  } else {
    clearStoredTokens();
  }
}

let client: LogtoClient | null = null;

async function logto(): Promise<LogtoClient> {
  if (!LOGTO_ENDPOINT || !LOGTO_APP_ID) {
    throw new Error('缺少 VITE_LOGTO_ENDPOINT / VITE_LOGTO_APP_ID');
  }
  client ??= new LogtoClient({
    endpoint: LOGTO_ENDPOINT,
    appId: LOGTO_APP_ID,
    ...(LOGTO_RESOURCE ? { resources: [LOGTO_RESOURCE] } : {}),
  });
  return client;
}

/** 当前 access token;拿不到返回 null(由调用方决定是否带 Authorization)。 */
export async function accessToken(): Promise<string | null> {
  if (MODE === 'session') {
    return (await sessionAuthenticated()) ? 'session' : null;
  }
  if (MODE === 'token') {
    return getStoredToken();
  }
  if (MODE === 'logto') {
    const logtoClient = await logto();
    if (!(await logtoClient.isAuthenticated())) {
      return null;
    }
    return logtoClient.getAccessToken(LOGTO_RESOURCE);
  }
  return null;
}

let refreshing: Promise<boolean> | null = null;

/**
 * 用 refresh token 换新令牌对(单飞:并发 401 只发一次请求)。
 * 没存 refresh(粘贴令牌场景)、刷新失败或被吊销 → 清空并返回 false。
 * 裸 fetch 不走 `@/lib/http`,避免"刷新失败 → 重试刷新"循环依赖。
 */
export function refreshAccessToken(): Promise<boolean> {
  refreshing ??= doRefresh().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function doRefresh(): Promise<boolean> {
  if (isSession()) {
    try {
      const response = await sessionFetch('/api/auth/refresh', {
        method: 'POST',
      });
      return response.ok;
    } catch {
      return false;
    }
  }
  const token = getStoredRefreshToken();
  if (!token) {
    return false;
  }
  try {
    const response = await fetch(
      `${ACCOUNT_BASE}/account-api/v1/auth/refresh`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      },
    );
    if (!response.ok) {
      // 401 = 过期/被吊销/重用检测:后端已切断,前端同步清空
      clearStoredTokens();
      return false;
    }
    const body = (await response.json()) as {
      access_token: string;
      refresh_token: string;
    };
    setStoredTokens(body.access_token, body.refresh_token);
    return true;
  } catch {
    // 网络错误等:令牌仍在,下次再试
    return false;
  }
}

/**
 * 请求头:有 token 走 Bearer(租户在 claim 里);没有就空着头发——
 * 未登录的访问由守卫拦在页面之外,这里不做兜底。
 */
export async function authHeaders(): Promise<Record<string, string>> {
  if (isSession()) {
    return csrfHeaders();
  }
  const token = await accessToken();
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

/** OIDC 回调地址:需在 Logto 控制台登记。 */
export function callbackUri(): string {
  return `${window.location.origin}/callback`;
}

const LOGIN_REDIRECT_KEY = 'beauty.auth.login_redirect';

/** 记住登录成功后要回到的站内路径(登录页写入,回调页消费)。 */
export function rememberLoginRedirect(path: string): void {
  window.sessionStorage.setItem(LOGIN_REDIRECT_KEY, path);
}

/** 取出并清掉登录来路:无记录或非站内路径一律回会话页。 */
export function takeLoginRedirect(): string {
  const value = window.sessionStorage.getItem(LOGIN_REDIRECT_KEY);
  window.sessionStorage.removeItem(LOGIN_REDIRECT_KEY);
  return value?.startsWith('/') && !value.startsWith('//') ? value : '/';
}

export async function signIn(): Promise<void> {
  await (await logto()).signIn({ redirectUri: callbackUri() });
}

export async function handleSignInCallback(href: string): Promise<void> {
  await (await logto()).handleSignInCallback(href);
}

export async function signOut(): Promise<void> {
  if (MODE === 'session') {
    try {
      await sessionFetch('/api/auth/logout', { method: 'POST' });
    } catch {
      // 网络失败不阻塞本地跳转
    }
    return;
  }
  if (MODE === 'logto') {
    await (await logto()).signOut(window.location.origin);
    return;
  }
  if (MODE === 'token') {
    // 尽力吊销 refresh token(失败不阻塞,本地令牌反正要清)
    const token = getStoredRefreshToken();
    if (token) {
      void fetch(`${ACCOUNT_BASE}/account-api/v1/auth/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      }).catch(() => undefined);
    }
  }
  clearStoredTokens();
}

/** 已登录账号展示名;未登录返回 null。 */
export async function sessionAuthenticated(): Promise<boolean> {
  try {
    const response = await sessionFetch('/api/auth/session');
    return response.ok;
  } catch {
    return false;
  }
}

export async function accountLabel(): Promise<string | null> {
  if (MODE === 'session') {
    try {
      const response = await sessionFetch('/api/auth/session');
      if (!response.ok) return null;
      const body = (await response.json()) as {
        user?: { display_name?: string | null; email?: string };
      };
      return body.user?.display_name ?? body.user?.email ?? '已登录';
    } catch {
      return null;
    }
  }
  if (MODE === 'token') {
    if (!getStoredToken()) {
      return null;
    }
    const label = window.localStorage.getItem(LABEL_KEY);
    return label ?? '手动令牌';
  }
  if (MODE !== 'logto') {
    return null;
  }
  try {
    const logtoClient = await logto();
    if (!(await logtoClient.isAuthenticated())) {
      return null;
    }
    const claims = await logtoClient.getIdTokenClaims();
    return (claims.email as string | undefined) ?? claims.sub ?? null;
  } catch {
    return null;
  }
}
