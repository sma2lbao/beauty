import { afterEach, describe, expect, it, vi } from 'vitest';

import { accountApi } from './account-api';
import { setStoredToken } from './auth';
import { ApiError } from './http';

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
  window.localStorage.clear();
});

describe('accountApi', () => {
  it('当前账号走 /account-api 前缀并带 Bearer 鉴权头', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, {
      id: 'a'.repeat(32),
      email: 'me@example.com',
      display_name: '小月',
      status: 'active',
      created_at: '2026-10-13T00:00:00+00:00',
    });

    await accountApi.me();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/me');
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer test-jwt',
    });
  });

  it('更新账号只传 display_name', async () => {
    const fetchMock = mockFetch(200, {});

    await accountApi.updateMe({ display_name: '新月' });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/me');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ display_name: '新月' });
  });

  it('登记会话只传 device(账号由 token 决定)', async () => {
    const fetchMock = mockFetch(201, {});

    await accountApi.addSession({ device: 'MacBook / Chrome' });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/sessions');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      device: 'MacBook / Chrome',
    });
  });

  it('吊销会话走 POST /revoke', async () => {
    const fetchMock = mockFetch(200, {});

    await accountApi.revokeSession('s1');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/sessions/s1/revoke');
    expect(init.method).toBe('POST');
  });

  it('冲突映射为 ApiError(409 邮箱已注册)', async () => {
    mockFetch(409, { code: 'account.email_exists', message: '邮箱已注册' });

    const error = (await accountApi
      .register({ email: 'dup@example.com', password: 'hunter2ok' })
      .catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('account.email_exists');
  });
});
