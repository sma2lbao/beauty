/**
 * 开放平台 API Key(M3-01):同源 /api/account/v1/api-keys(BFF 代理)。
 */
import { createHttpClient } from '@/lib/http';

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

const http = createHttpClient(PATH);

export const apiKeysApi = {
  list: () => http.request<ApiKey[]>(),
  create: (name: string, scopes: string[]) =>
    http.request<ApiKeyCreated>('', {
      method: 'POST',
      body: JSON.stringify({ name, scopes }),
    }),
  rotate: (id: string) =>
    http.request<ApiKeyCreated>('/' + id + '/rotate', { method: 'POST' }),
  revoke: (id: string) => http.request<void>('/' + id, { method: 'DELETE' }),
};
