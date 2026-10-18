/**
 * 连接器数据层:同源 /api/connector/*,BFF 转发 connector-service。
 * 凭证只在创建时提交,读端永远不返回。
 */
import { requestJson } from '@/lib/http';

export type Connection = {
  id: string;
  provider: string;
  account: string;
  display_name: string | null;
  status: 'active' | 'revoked' | 'expired';
  last_checked_at: string | null;
  created_at: string;
};

export type EmailSummary = {
  subject: string;
  sender: string;
  sent_at: string;
  snippet: string;
  id: string;
  thread_id: string;
  has_attachments: boolean;
};

export type OAuthProvider = {
  provider: string;
  label: string;
  enabled: boolean;
};

export type ConnectionInput = {
  provider: 'imap';
  account: string;
  display_name?: string | null;
  credentials: {
    host: string;
    port?: number;
    username: string;
    password: string;
    smtp_host?: string | null;
    smtp_port?: number | null;
  };
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>('', '/api/connector' + path, init);
}

export const connectorApi = {
  list: () => request<Connection[]>('/connections'),
  create: (input: ConnectionInput) =>
    request<Connection>('/connections', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  remove: (id: string) =>
    request<Connection>('/connections/' + id, { method: 'DELETE' }),
  test: (id: string) =>
    request<{ ok: boolean; detail: string }>('/connections/' + id + '/test', {
      method: 'POST',
    }),
  oauthProviders: () => request<OAuthProvider[]>('/oauth/providers'),
  startOAuth: (provider: string) =>
    request<{ authorize_url: string }>('/oauth/' + provider + '/start'),
  emails: (id: string, limit = 10) =>
    request<EmailSummary[]>(
      '/connections/' + id + '/emails?limit=' + String(limit),
    ),
  sendEmail: (
    id: string,
    input: { to: string; subject: string; body: string },
  ) =>
    request<{ ok: boolean; detail: string }>('/connections/' + id + '/emails', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
};
