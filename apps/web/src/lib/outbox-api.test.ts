import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './http';
import { outboxApi } from './outbox-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
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
  vi.restoreAllMocks();
});

describe('outboxApi', () => {
  it('计数与列表走 /api/v1/outbox 且带鉴权头', async () => {
    const fetchMock = mockFetch(200, { pending: 1, dispatched: 2, failed: 0 });

    await outboxApi.stats();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/outbox/stats');
    expect(JSON.stringify(init.headers)).toContain('X-Tenant-Id');
  });

  it('列表可按状态过滤并带 limit', async () => {
    const fetchMock = mockFetch(200, []);

    await outboxApi.events('failed', 20);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/outbox/events?limit=20&status=failed',
    );
  });

  it('不传状态时不带 status 参数', async () => {
    const fetchMock = mockFetch(200, []);

    await outboxApi.events();

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/outbox/events?limit=50');
  });

  it('重放单条走 POST /events/{id}/retry', async () => {
    const fetchMock = mockFetch(200, { id: 'e1', status: 'pending' });

    await outboxApi.retry('e1');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/outbox/events/e1/retry');
    expect(init.method).toBe('POST');
  });

  it('批量重放返回条数', async () => {
    mockFetch(200, { requeued: 3 });

    await expect(outboxApi.retryFailed()).resolves.toEqual({ requeued: 3 });
  });

  it('outbox 未启用时映射为 409 ApiError', async () => {
    mockFetch(409, { code: 'outbox.disabled', message: 'outbox 未启用' });

    const error = (await outboxApi.stats().catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('outbox.disabled');
  });
});
