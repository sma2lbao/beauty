/**
 * 账号控制台的数据层:account-service 的 API 契约(架构 §3)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_ACCOUNT_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/account-api` 代理到 :8001,见 vite.config.ts)。
 */

import { authMode } from '@/lib/auth';
import { createHttpClient } from '@/lib/http';
import { accountBase as BASE } from '@/config/env';

const PREFIX = authMode() === 'session' ? '/api/account' : '/account-api';

export type Role = 'owner' | 'member';
export type MemberStatus = 'active' | 'disabled';

export type Organization = {
  id: string;
  name: string;
  plan: string;
  quota: Record<string, unknown>;
  created_at: string;
};

export type Member = {
  id: string;
  subject: string;
  email: string;
  display_name: string | null;
  role: Role;
  status: MemberStatus;
  created_at: string;
};

export type Session = {
  id: string;
  user_id: string;
  device: string;
  status: 'active' | 'revoked';
  last_seen_at: string | null;
  created_at: string;
};

export type AuthResult = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  tenant_id: string;
  organization_id: string;
  user_id: string;
  subject: string;
  role: Role;
  session_id: string;
};

const http = createHttpClient(BASE);

export const accountApi = {
  organization: () => http.request<Organization>(`${PREFIX}/v1/organization`),
  updateOrganization: (patch: {
    name?: string;
    plan?: string;
    quota?: Record<string, unknown>;
  }) =>
    http.request<Organization>(`${PREFIX}/v1/organization`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  members: () => http.request<Member[]>(`${PREFIX}/v1/users`),
  addMember: (body: {
    subject: string;
    email: string;
    display_name?: string | null;
    role?: Role;
  }) =>
    http.request<Member>(`${PREFIX}/v1/users`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateMember: (
    memberId: string,
    patch: { display_name?: string; role?: Role; status?: MemberStatus },
  ) =>
    http.request<Member>(`${PREFIX}/v1/users/${memberId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  sessions: () => http.request<Session[]>(`${PREFIX}/v1/sessions`),
  addSession: (body: { user_id: string; device: string }) =>
    http.request<Session>(`${PREFIX}/v1/sessions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  revokeSession: (sessionId: string) =>
    http.request<Session>(`${PREFIX}/v1/sessions/${sessionId}/revoke`, {
      method: 'POST',
    }),
  /** 注册:新组织 + owner 成员,返回双令牌(409 = 邮箱已注册)。 */
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
