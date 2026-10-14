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
  it('组织档案走 /account-api 前缀并带 Bearer 鉴权头', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, {
      id: 'org-1',
      name: '示例科技',
      plan: 'free',
      quota: {},
      created_at: '2026-10-13T00:00:00+00:00',
    });

    await accountApi.organization();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/organization');
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer test-jwt',
    });
  });

  it('更新组织只传给出的字段', async () => {
    const fetchMock = mockFetch(200, {});

    await accountApi.updateOrganization({ name: '新名字', quota: { seats: 20 } });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/organization');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({
      name: '新名字',
      quota: { seats: 20 },
    });
  });

  it('新增成员发送 subject/email/role', async () => {
    const fetchMock = mockFetch(201, {});

    await accountApi.addMember({
      subject: 'idp|u1',
      email: 'u1@example.com',
      display_name: null,
      role: 'owner',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/users');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      subject: 'idp|u1',
      email: 'u1@example.com',
      display_name: null,
      role: 'owner',
    });
  });

  it('吊销会话走 POST /revoke', async () => {
    const fetchMock = mockFetch(200, {});

    await accountApi.revokeSession('s1');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/account-api/v1/sessions/s1/revoke');
    expect(init.method).toBe('POST');
  });

  it('冲突映射为 ApiError(409 重复成员)', async () => {
    mockFetch(409, { code: 'account.user_exists', message: '该成员已存在' });

    const error = (await accountApi
      .addMember({ subject: 'idp|u1', email: 'u1@example.com' })
      .catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('account.user_exists');
  });
});
