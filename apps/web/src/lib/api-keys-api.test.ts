import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiKeysApi } from './api-keys-api';

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiKeysApi', () => {
  it('创建/轮换/吊销走 /api/account/v1/api-keys', async () => {
    const createMock = mockFetch(201, { id: 'k1', key: 'bm_live_x' });
    await apiKeysApi.create('mcp', ['mcp:tools:read']);
    const [createUrl, createInit] = createMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(createUrl).toBe('/api/account/v1/api-keys');
    expect(createInit.method).toBe('POST');

    const rotateMock = mockFetch(200, { id: 'k2' });
    await apiKeysApi.rotate('k1');
    expect(rotateMock.mock.calls[0]?.[0]).toBe(
      '/api/account/v1/api-keys/k1/rotate',
    );

    const revokeMock = mockFetch(204);
    await apiKeysApi.revoke('k1');
    expect(revokeMock.mock.calls[0]?.[0]).toBe('/api/account/v1/api-keys/k1');
    expect((revokeMock.mock.calls[0]?.[1] as RequestInit).method).toBe(
      'DELETE',
    );
  });
});
