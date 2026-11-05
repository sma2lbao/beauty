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

  it('BYOK 保存(含密钥)同时整体提交 models', async () => {
    const saveMock = mockFetch(200, { provider: 'openai' });
    await insightsApi.saveByok('openai', 'sk-secret-1234', [
      'openai/gpt-4o-mini',
    ]);
    const [url, init] = saveMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/byok/openai');
    expect(init.method).toBe('PUT');
    expect(init.body).toBe(
      JSON.stringify({
        api_key: 'sk-secret-1234',
        models: ['openai/gpt-4o-mini'],
      }),
    );

    const deleteMock = mockFetch(204);
    await insightsApi.deleteByok('openai');
    const [deleteUrl, deleteInit] = deleteMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(deleteUrl).toBe('/api/byok/openai');
    expect(deleteInit.method).toBe('DELETE');
  });

  it('BYOK 保存:apiKey 为 null 时不带 api_key,只提交 models', async () => {
    const saveMock = mockFetch(200, { provider: 'openai' });
    await insightsApi.saveByok('openai', null, ['openai/gpt-4o-mini']);

    const [url, init] = saveMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/byok/openai');
    expect(init.body).toBe(JSON.stringify({ models: ['openai/gpt-4o-mini'] }));
    // 留空密钥时请求体里不应出现 api_key 键
    expect(JSON.parse(init.body as string)).not.toHaveProperty('api_key');
  });
});
