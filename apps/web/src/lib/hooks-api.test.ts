import { afterEach, describe, expect, it, vi } from 'vitest';

import { hooksApi, type Hook } from './hooks-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const hook: Hook = {
  id: 'h1',
  event: 'PreToolUse',
  matcher: '*',
  kind: 'webhook',
  target: 'https://example.com/dsh/hook',
  timeout_seconds: 3,
  enabled: true,
  created_at: '2026-11-10T00:00:00+00:00',
  updated_at: '2026-11-10T00:00:00+00:00',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('hooksApi', () => {
  it('list 读 GET /api/v1/hooks', async () => {
    const fetchMock = mockFetch(200, [hook]);

    await expect(hooksApi.list()).resolves.toEqual([hook]);

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/hooks');
  });

  it('create POST 同一路径并返回一次性 secret', async () => {
    const fetchMock = mockFetch(201, { ...hook, secret: 's3cret' });
    const input = {
      event: 'PreToolUse' as const,
      target: 'https://example.com/dsh/hook',
    };

    const created = await hooksApi.create(input);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/hooks');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual(input);
    expect(created.secret).toBe('s3cret');
  });

  it('update 只 PATCH 局部字段', async () => {
    const fetchMock = mockFetch(200, { ...hook, enabled: false });

    const updated = await hooksApi.update('h1', { enabled: false });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/hooks/h1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ enabled: false });
    expect(updated.enabled).toBe(false);
  });

  it('remove DELETE 同一路径并接受 204 空体', async () => {
    const fetchMock = mockFetch(204);

    await expect(hooksApi.remove('h1')).resolves.toBeUndefined();

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/hooks/h1');
    expect(init.method).toBe('DELETE');
  });

  it('rotate POST 到 rotate 子路径并返回新 secret', async () => {
    const fetchMock = mockFetch(200, { ...hook, secret: 'rotated' });

    const rotated = await hooksApi.rotate('h1');

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/hooks/h1/rotate');
    expect(init.method).toBe('POST');
    expect(rotated.secret).toBe('rotated');
  });
});
