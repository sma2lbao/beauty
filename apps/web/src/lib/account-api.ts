/**
 * 账号控制台的数据层:account-service 的 API 契约(架构 §3)。
 *
 * 账号即隔离边界(单用户):account_id 就是 users.id,数据按账号隔离,
 * 跨账号一律 404;账号即唯一身份与隔离边界。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_ACCOUNT_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/account-api` 代理到 :8001,见 vite.config.ts)。
 */

import { authMode } from '@/lib/auth';
import { createHttpClient } from '@/lib/http';
import { accountBase as BASE } from '@/config/env';

const PREFIX = authMode() === 'session' ? '/api/account' : '/account-api';

/** 当前账号(GET /v1/me)。 */
export type Account = {
  id: string;
  email: string;
  /** 服务端保证是字符串(未设置时回落邮箱前缀)。 */
  display_name: string;
  status: 'active' | 'disabled';
  /**
   * 平台角色(P0-7):普通用户 / 平台管理员。
   * 只用于前端入口的显示层判断;权限由服务端每请求查库校验。
   */
  role: 'user' | 'admin';
  created_at: string;
};

/** 一条设备会话;账号由 access token 决定,响应里不重复暴露。 */
export type Session = {
  id: string;
  device: string;
  status: 'active' | 'revoked';
  last_seen_at: string | null;
  created_at: string;
};

/** 注册/登录返回的扁平令牌组(账号即唯一身份)。 */
export type AuthResult = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  account_id: string;
  subject: string;
  session_id: string;
};

const http = createHttpClient(BASE);

export const accountApi = {
  me: () => http.request<Account>(`${PREFIX}/v1/me`),
  /** 只改显示名(其余字段由服务端维护)。 */
  updateMe: (patch: { display_name: string | null }) =>
    http.request<Account>(`${PREFIX}/v1/me`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  sessions: () => http.request<Session[]>(`${PREFIX}/v1/sessions`),
  /** 登记新会话:账号取当前 token 所属账号,只需描述设备。 */
  addSession: (body: { device: string }) =>
    http.request<Session>(`${PREFIX}/v1/sessions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  revokeSession: (sessionId: string) =>
    http.request<Session>(`${PREFIX}/v1/sessions/${sessionId}/revoke`, {
      method: 'POST',
    }),
  /** 注册:创建账号并返回双令牌(409 = 邮箱已注册)。 */
  register: (input: {
    email: string;
    password: string;
    display_name?: string;
  }) =>
    http.request<AuthResult>(`${PREFIX}/v1/auth/register`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  /** 登录:邮箱 + 密码换双令牌(access 过期由 http 层用 refresh 自动续)。 */
  login: (input: { email: string; password: string }) =>
    http.request<AuthResult>(`${PREFIX}/v1/auth/login`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
};
