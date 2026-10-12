/**
 * 前端鉴权接线(与后端 §10 的 token→租户约定配对)。
 *
 * 模式(`VITE_AUTH_MODE`):
 * - `dev`(默认):不带 token;后端鉴权关闭时用 `VITE_TENANT_ID` 临时租户头;
 * - `token`:手动粘贴 IdP access token(联调/运维),存 localStorage;
 * - `logto`:Logto 授权码 + PKCE 登录,access token 由 SDK 自动续期。
 *
 * 构建期环境变量:`VITE_AUTH_MODE`、`VITE_LOGTO_ENDPOINT`、`VITE_LOGTO_APP_ID`、
 * `VITE_LOGTO_RESOURCE`、`VITE_TENANT_ID`(见 apps/web/.env.example)。
 */

import LogtoClient from '@logto/browser';

export type AuthMode = 'dev' | 'token' | 'logto';

const MODE = (import.meta.env.VITE_AUTH_MODE as AuthMode | undefined) ?? 'dev';
const LOGTO_ENDPOINT = import.meta.env.VITE_LOGTO_ENDPOINT as string | undefined;
const LOGTO_APP_ID = import.meta.env.VITE_LOGTO_APP_ID as string | undefined;
const LOGTO_RESOURCE = import.meta.env.VITE_LOGTO_RESOURCE as string | undefined;
const TENANT =
  (import.meta.env.VITE_TENANT_ID as string | undefined) ?? 'demo-tenant';
const TOKEN_KEY = 'beauty.auth.access_token';

export function authMode(): AuthMode {
  return MODE;
}

export function tenantId(): string {
  return TENANT;
}

export function getStoredToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string | null): void {
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
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

export async function signIn(): Promise<void> {
  await (await logto()).signIn({ redirectUri: callbackUri() });
}

export async function handleSignInCallback(href: string): Promise<void> {
  await (await logto()).handleSignInCallback(href);
}

export async function signOut(): Promise<void> {
  if (MODE === 'logto') {
    await (await logto()).signOut(window.location.origin);
  }
  setStoredToken(null);
}

/** 已登录账号展示名;未登录返回 null。 */
export async function accountLabel(): Promise<string | null> {
  if (MODE === 'token') {
    return getStoredToken() ? '手动令牌' : null;
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
