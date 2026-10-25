import { afterEach, describe, expect, it, vi } from 'vitest';

import { setStoredToken } from './auth';
import { ApiError } from './http';
import { memoryApi } from './memory-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function calledUrl(fetchMock: ReturnType<typeof vi.fn>): URL {
  const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new URL(url, 'http://localhost');
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('memoryApi', () => {
  it('列表默认走 /api/v1/memories 且带 Bearer 鉴权头', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, []);

    await expect(memoryApi.list()).resolves.toEqual([]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/memories');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-jwt',
    });
  });

  it('列表按工作区/类型/关键词/上限过滤', async () => {
    const fetchMock = mockFetch(200, []);

    await memoryApi.list({
      workspaceId: 'ws-1',
      kind: 'fact',
      q: '合同',
      limit: 20,
    });

    const params = calledUrl(fetchMock).searchParams;
    expect(calledUrl(fetchMock).pathname).toBe('/api/v1/memories');
    expect(params.get('workspace_id')).toBe('ws-1');
    expect(params.get('kind')).toBe('fact');
    expect(params.get('q')).toBe('合同');
    expect(params.get('limit')).toBe('20');
  });

  it('检索走 GET /memories/search 并编码 query', async () => {
    const response = { query: '合同 条款', hits: [] };
    const fetchMock = mockFetch(200, response);

    await expect(
      memoryApi.search({ query: '合同 条款', kind: 'note', limit: 5 }),
    ).resolves.toEqual(response);

    const url = calledUrl(fetchMock);
    expect(url.pathname).toBe('/api/v1/memories/search');
    expect(url.searchParams.get('query')).toBe('合同 条款');
    expect(url.searchParams.get('kind')).toBe('note');
    expect(url.searchParams.get('limit')).toBe('5');
  });

  it('统计返回四类计数', async () => {
    const stats = { total: 4, active: 3, pinned: 1, expired: 1 };
    const fetchMock = mockFetch(200, stats);

    await expect(memoryApi.stats()).resolves.toEqual(stats);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe(
      '/api/v1/memories/stats',
    );
  });

  it('新建 POST 字段与后端 MemoryCreate 对齐', async () => {
    const memory = {
      id: 'm1',
      workspace_id: null,
      scope: 'tenant',
      kind: 'note',
      content: '默认使用中文回复',
      pinned: true,
      source_type: 'manual',
      source_id: null,
      expires_at: null,
      last_accessed_at: null,
      access_count: 0,
      created_at: '2026-10-20T08:00:00+00:00',
      updated_at: '2026-10-20T08:00:00+00:00',
    };
    const fetchMock = mockFetch(201, memory);

    await memoryApi.create({
      content: '默认使用中文回复',
      kind: 'note',
      scope: 'tenant',
      pinned: true,
      ttl_days: 30,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/memories');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      content: '默认使用中文回复',
      kind: 'note',
      scope: 'tenant',
      pinned: true,
      ttl_days: 30,
    });
  });

  it('删除走 DELETE /memories/{id} 且 204 解析为 undefined', async () => {
    const fetchMock = mockFetch(204);

    await expect(memoryApi.remove('m/1')).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/memories/m%2F1');
    expect(init.method).toBe('DELETE');
  });

  it('清理过期返回清理条数', async () => {
    const fetchMock = mockFetch(200, { purged: 2 });

    await expect(memoryApi.purgeExpired()).resolves.toEqual({ purged: 2 });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/memories/purge-expired');
    expect(init.method).toBe('POST');
  });

  it('删除不存在的记忆映射为 404 ApiError', async () => {
    mockFetch(404, { code: 'memory.not_found', message: '记忆不存在' });

    const error = (await memoryApi
      .remove('nope')
      .catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.code).toBe('memory.not_found');
  });
});
