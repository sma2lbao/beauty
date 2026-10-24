import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  createHttpClient,
  isUnauthorized,
  requestJson,
} from '@/lib/http';

const mocks = vi.hoisted(() => ({
  authHeaders: vi.fn(async () => ({})),
  refreshAccessToken: vi.fn(async () => false),
}));

vi.mock('@/lib/auth', () => ({
  authHeaders: mocks.authHeaders,
  refreshAccessToken: mocks.refreshAccessToken,
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  mocks.authHeaders.mockResolvedValue({});
  mocks.refreshAccessToken.mockResolvedValue(false);
});

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe('requestJson', () => {
  it('2xx:解析 JSON,204 返回 undefined', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ value: 42 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchSpy);

    expect(await requestJson<{ value: number }>('', '/v1/x')).toEqual({
      value: 42,
    });
    expect(await requestJson<void>('', '/v1/x')).toBeUndefined();
  });

  it('非 2xx:抛 ApiError,携带后端错误契约', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ code: 'run.not_found', message: 'run 不存在' }, 404),
        ),
    );

    const error = await requestJson('', '/v1/x').catch(
      (cause: unknown) => cause,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(isUnauthorized(error)).toBe(false);
    expect((error as ApiError).code).toBe('run.not_found');
    expect((error as ApiError).message).toBe('run 不存在');
  });

  it('401 且续期成功:换新令牌重试一次', async () => {
    mocks.refreshAccessToken.mockResolvedValue(true);
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ code: 'auth.token_invalid', message: '过期' }, 401),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);

    expect(await requestJson<{ ok: boolean }>('', '/v1/x')).toEqual({
      ok: true,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(mocks.refreshAccessToken).toHaveBeenCalledTimes(1);
  });

  it('401 且无法续期:不重试,按错误契约抛出', async () => {
    mocks.refreshAccessToken.mockResolvedValue(false);
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ code: 'auth.token_invalid', message: '过期' }, 401),
      );
    vi.stubGlobal('fetch', fetchSpy);

    const error = await requestJson('', '/v1/x').catch(
      (cause: unknown) => cause,
    );
    expect(isUnauthorized(error)).toBe(true);
    expect((error as ApiError).status).toBe(401);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('续期成功但重试仍 401:抛 401,不再循环', async () => {
    mocks.refreshAccessToken.mockResolvedValue(true);
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ code: 'auth.token_invalid', message: '过期' }, 401),
      );
    vi.stubGlobal('fetch', fetchSpy);

    const error = await requestJson('', '/v1/x').catch(
      (cause: unknown) => cause,
    );
    expect(isUnauthorized(error)).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(mocks.refreshAccessToken).toHaveBeenCalledTimes(1);
  });

  it('FormData 请求体:不设置 application/json Content-Type', async () => {
    mocks.authHeaders.mockResolvedValue({ Authorization: 'Bearer token' });
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse({ id: 'p1' }));
    vi.stubGlobal('fetch', fetchSpy);

    const form = new FormData();
    form.append('file', new File(['# skill'], 'demo.zip'));
    await requestJson('', '/v1/skills/packages', {
      method: 'POST',
      body: form,
    });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/v1/skills/packages');
    expect(init.body).toBe(form);
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer token');
    expect(headers['Content-Type']).toBeUndefined();
  });

  it('非 401 错误不触发续期', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(jsonResponse({ code: 'server.error' }, 500));
    vi.stubGlobal('fetch', fetchSpy);

    await requestJson('', '/v1/x').catch(() => undefined);
    expect(mocks.refreshAccessToken).not.toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe('createHttpClient', () => {
  it('绑定 base:request 用 `${base}${path}` 拼出完整 URL', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);

    const http = createHttpClient('http://api.test');
    expect(await http.request<{ ok: boolean }>('/v1/x')).toEqual({ ok: true });

    const [url] = fetchSpy.mock.calls[0] as unknown as [string];
    expect(url).toBe('http://api.test/v1/x');
  });

  it('204 与 requestJson 一致:返回 undefined', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );

    const http = createHttpClient('');
    expect(await http.request<void>('/v1/x')).toBeUndefined();
  });

  it('401 且续期成功:走既有单飞续期并重试一次', async () => {
    mocks.refreshAccessToken.mockResolvedValue(true);
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ code: 'auth.token_invalid', message: '过期' }, 401),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);

    const http = createHttpClient('http://api.test');
    expect(await http.request<{ ok: boolean }>('/v1/x')).toEqual({ ok: true });
    expect(mocks.refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
