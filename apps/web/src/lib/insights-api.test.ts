import { afterEach, describe, expect, it, vi } from 'vitest';

import { insightsApi } from './insights-api';

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn(async () => ({
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

describe('insightsApi', () => {
  it('模型列表与用量走同源 /api', async () => {
    const modelsMock = mockFetch(200, { object: 'list', data: [] });
    await insightsApi.models();
    expect((modelsMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/models',
    );

    const usageMock = mockFetch(200, { today_tokens: 1 });
    await insightsApi.usage();
    expect((usageMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/usage/summary',
    );
  });

  it('BYOK 保存/删除', async () => {
    const saveMock = mockFetch(200, { provider: 'openai' });
    await insightsApi.saveByok('openai', 'sk-secret-1234');
    const [url, init] = saveMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/byok/openai');
    expect(init.method).toBe('PUT');
    expect(init.body).toBe(JSON.stringify({ api_key: 'sk-secret-1234' }));

    const deleteMock = mockFetch(204);
    await insightsApi.deleteByok('openai');
    const [deleteUrl, deleteInit] = deleteMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(deleteUrl).toBe('/api/byok/openai');
    expect(deleteInit.method).toBe('DELETE');
  });
});
