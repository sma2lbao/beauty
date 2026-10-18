/**
 * 开放平台 API Key(M3-01):同源 /api/account/v1/api-keys(BFF 代理)。
 */
import { requestJson } from '@/lib/http';

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  key_hint: string;
  masked_key: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
};

export type ApiKeyCreated = ApiKey & { key: string };

const PATH = '/api/account/v1/api-keys';

function request<T>(path = '', init?: RequestInit): Promise<T> {
  return requestJson<T>('', PATH + path, init);
}

export const apiKeysApi = {
  list: () => request<ApiKey[]>(),
  create: (name: string, scopes: string[]) =>
    request<ApiKeyCreated>('', {
      method: 'POST',
      body: JSON.stringify({ name, scopes }),
    }),
  rotate: (id: string) =>
    request<ApiKeyCreated>('/' + id + '/rotate', { method: 'POST' }),
  revoke: (id: string) => request<void>('/' + id, { method: 'DELETE' }),
};
