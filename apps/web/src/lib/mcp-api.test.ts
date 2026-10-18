import { afterEach, describe, expect, it, vi } from 'vitest';

import { mcpApi } from './mcp-api';

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

describe('mcpApi', () => {
  it('列表/注册/测试/删除', async () => {
    const listMock = mockFetch(200, []);
    await mcpApi.list();
    expect(listMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers');

    const createMock = mockFetch(201, { id: 'm1' });
    await mcpApi.create({ name: 'demo', url: 'http://127.0.0.1:8010/mcp' });
    const [createUrl, createInit] = createMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(createUrl).toBe('/api/v1/mcp/servers');
    expect(createInit.method).toBe('POST');

    const testMock = mockFetch(200, { ok: true, tools: [] });
    await mcpApi.test('m1');
    expect(testMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers/m1/test');

    const deleteMock = mockFetch(204);
    await mcpApi.remove('m1');
    expect(deleteMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers/m1');
  });
});
