/**
 * 前端鉴权接线(与后端 §10 的 token→租户约定配对)。
 *
 * 模式(`VITE_AUTH_MODE`):
 * - `dev`(默认):不带 token;后端鉴权关闭时用 `VITE_TENANT_ID` 临时租户头;
 * - `token`:自建认证登录(邮箱 + 密码,注册见登录页),双令牌存 localStorage:
 *   access 过期后由 `@/lib/http` 拿 refresh 自动续(旋转式);运维仍可粘贴
 *   纯 access token(联调用,无续期能力);
 * - `logto`:Logto 授权码 + PKCE 登录,access token 由 SDK 自动续期。
 *
 * 构建期环境变量:`VITE_AUTH_MODE`、`VITE_LOGTO_ENDPOINT`、`VITE_LOGTO_APP_ID`、
 * `VITE_LOGTO_RESOURCE`、`VITE_TENANT_ID`、`VITE_ACCOUNT_API_URL`(见 apps/web/.env.example)。
 */

import LogtoClient from '@logto/browser';

export type AuthMode = 'dev' | 'token' | 'logto';

const MODE = (import.meta.env.VITE_AUTH_MODE as AuthMode | undefined) ?? 'dev';
const LOGTO_ENDPOINT = import.meta.env.VITE_LOGTO_ENDPOINT as string | undefined;
const LOGTO_APP_ID = import.meta.env.VITE_LOGTO_APP_ID as string | undefined;
const LOGTO_RESOURCE = import.meta.env.VITE_LOGTO_RESOURCE as string | undefined;
const TENANT =
  (import.meta.env.VITE_TENANT_ID as string | undefined) ?? 'demo-tenant';
const ACCOUNT_BASE =
  (import.meta.env.VITE_ACCOUNT_API_URL as string | undefined) ?? '';
const TOKEN_KEY = 'beauty.auth.access_token';
const REFRESH_KEY = 'beauty.auth.refresh_token';
const LABEL_KEY = 'beauty.auth.account_label';

export function authMode(): AuthMode {
  return MODE;
}

export function tenantId(): string {
  return TENANT;
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
  const token = getStoredRefreshToken();
  if (!token) {
    return false;
  }
  try {
    const response = await fetch(`${ACCOUNT_BASE}/account-api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    });
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

/** 请求头:有 token 走 Bearer(后端鉴权开启);dev 模式退回临时租户头。 */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return MODE === 'dev' ? { 'X-Tenant-Id': TENANT } : {};
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

/** 取出并清掉登录来路:无记录或非站内路径一律回对话页。 */
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
export async function accountLabel(): Promise<string | null> {
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
