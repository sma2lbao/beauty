import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  checkOutcome,
  connectorApi,
  connectorsReturnUrl,
  errorCodeOf,
  isReconnectCode,
  isReconnectError,
  normalizeProviderCatalog,
} from './connector-api';

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function bodyOf(fetchMock: ReturnType<typeof mockFetch>, index = 0): unknown {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('connectorApi', () => {
  it('provider 目录与连接列表走同源网关', async () => {
    const providersMock = mockFetch(200, { providers: [] });
    await connectorApi.listProviders();
    expect(providersMock.mock.calls[0]?.[0]).toBe('/api/connector/connectors');

    const listMock = mockFetch(200, []);
    await connectorApi.listConnections();
    expect(listMock.mock.calls[0]?.[0]).toBe('/api/connector/connections');
  });

  it('目录响应兼容 {providers} 包封与 name 字段', async () => {
    mockFetch(200, {
      providers: [
        {
          name: 'demo',
          display_name: '示例连接器',
          description: '示例连接器',
          auth_kind: 'oauth_user',
          scopes: ['demo:read'],
          configured: true,
          token_connectable: true,
          docs_url: 'https://example.com/docs',
          connected: true,
          tools: [
            {
              name: 'Demo__get_me',
              title: '取当前用户',
              description: '返回当前用户信息',
              risk: 'read',
            },
          ],
        },
      ],
    });

    const providers = await connectorApi.listProviders();
    expect(providers).toHaveLength(1);
    expect(providers[0]).toMatchObject({
      provider: 'demo',
      display_name: '示例连接器',
      auth_kind: 'oauth_user',
      scopes: ['demo:read'],
      configured: true,
      token_connectable: true,
      docs_url: 'https://example.com/docs',
      connected: true,
    });
    expect(providers[0]?.tools[0]?.title).toBe('取当前用户');
  });

  it('目录也接受裸数组与 provider 字段(旧契约)', () => {
    expect(
      normalizeProviderCatalog([
        {
          provider: 'notion',
          display_name: 'Notion',
          description: '',
          auth_kind: 'oauth_user',
          scopes: [],
          tools: [],
        },
      ]),
    ).toEqual([
      {
        provider: 'notion',
        display_name: 'Notion',
        description: '',
        auth_kind: 'oauth_user',
        scopes: [],
        tools: [],
        configured: undefined,
        token_connectable: undefined,
        docs_url: null,
        connected: undefined,
        connection: null,
      },
    ]);
  });

  it('authorize 发送 provider 路径与 redirect_uri', async () => {
    const fetchMock = mockFetch(200, {
      provider: 'demo',
      authorize_url: 'https://auth.example.com/oauth',
      expires_at: '2026-11-03T10:05:00+00:00',
    });
    const result = await connectorApi.authorize('demo', {
      redirect_uri: 'http://localhost/connectors',
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/connector/connectors/demo/authorize');
    expect(init.method).toBe('POST');
    expect(bodyOf(fetchMock)).toEqual({
      redirect_uri: 'http://localhost/connectors',
    });
    expect(result.authorize_url).toBe('https://auth.example.com/oauth');
  });

  it('connectWithToken 发送 provider/token 到 /connections', async () => {
    const fetchMock = mockFetch(201, {
      id: 'c9',
      provider: 'notion',
      status: 'active',
      auth_kind: 'token',
      account: '我的 Notion',
      enabled: true,
    });

    const connection = await connectorApi.connectWithToken({
      provider: 'notion',
      token: 'secret_abc',
      display_name: '我的 Notion',
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/connector/connections');
    expect(init.method).toBe('POST');
    expect(bodyOf(fetchMock)).toEqual({
      provider: 'notion',
      token: 'secret_abc',
      display_name: '我的 Notion',
    });
    expect(connection.auth_kind).toBe('token');
  });

  it('详情/更新/删除/刷新/检查的路径与方法', async () => {
    const getMock = mockFetch(200, { id: 'c1' });
    await connectorApi.getConnection('c1');
    expect(getMock.mock.calls[0]?.[0]).toBe('/api/connector/connections/c1');
    expect((getMock.mock.calls[0]?.[1] as RequestInit).method).toBeUndefined();

    const patchMock = mockFetch(200, { id: 'c1' });
    await connectorApi.updateConnection('c1', {
      display_name: '工作账号',
      enabled: false,
    });
    expect(patchMock.mock.calls[0]?.[0]).toBe('/api/connector/connections/c1');
    expect((patchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('PATCH');
    expect(bodyOf(patchMock)).toEqual({
      display_name: '工作账号',
      enabled: false,
    });

    const deleteMock = mockFetch(204);
    await connectorApi.removeConnection('c1');
    expect(deleteMock.mock.calls[0]?.[0]).toBe('/api/connector/connections/c1');
    expect((deleteMock.mock.calls[0]?.[1] as RequestInit).method).toBe(
      'DELETE',
    );

    const refreshMock = mockFetch(200, { expires_at: null });
    await connectorApi.refreshConnection('c1');
    expect(refreshMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/refresh',
    );
    expect((refreshMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');

    const checkMock = mockFetch(200, { ok: true });
    await connectorApi.checkConnection('c1');
    expect(checkMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/check',
    );
    expect((checkMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');
  });

  it('provider 名与 id 做 URL 编码', async () => {
    const fetchMock = mockFetch(200, []);
    await connectorApi.authorize('a/b');
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connectors/a%2Fb/authorize',
    );
  });

  it('授权类错误暴露错误码并判定为需要重新连接', async () => {
    mockFetch(409, {
      code: 'connector.auth_expired',
      message: '授权已过期',
    });

    const failure = await connectorApi
      .listConnections()
      .catch((error) => error);
    expect(errorCodeOf(failure)).toBe('connector.auth_expired');
    expect(isReconnectError(failure)).toBe(true);
  });

  it('上游错误不算需要重新连接', async () => {
    mockFetch(429, {
      code: 'connector.upstream_rate_limited',
      message: '限流',
    });

    const failure = await connectorApi
      .checkConnection('c1')
      .catch((error) => error);
    expect(errorCodeOf(failure)).toBe('connector.upstream_rate_limited');
    expect(isReconnectError(failure)).toBe(false);
  });
});

describe('checkOutcome', () => {
  it('新契约:从返回的连接对象读 last_error_code', () => {
    expect(
      checkOutcome({
        id: 'c1',
        provider: 'demo',
        status: 'expired',
        last_error_code: 'connector.auth_expired',
      }),
    ).toEqual({
      ok: false,
      detail: 'connector.auth_expired',
      reconnect: true,
    });

    expect(
      checkOutcome({
        id: 'c1',
        provider: 'demo',
        status: 'active',
        last_error_code: null,
      }),
    ).toEqual({ ok: true, detail: null, reconnect: false });
  });

  it('旧契约:{ok,detail,error_code} 形状仍可用', () => {
    expect(
      checkOutcome({
        ok: false,
        detail: '上游限流',
        error_code: 'connector.upstream_rate_limited',
      }),
    ).toEqual({ ok: false, detail: '上游限流', reconnect: false });
  });
});

describe('connector 错误码工具', () => {
  it('只把 auth/scope 类错误判为重新连接', () => {
    expect(isReconnectCode('connector.auth_required')).toBe(true);
    expect(isReconnectCode('connector.auth_expired')).toBe(true);
    expect(isReconnectCode('connector.auth_revoked')).toBe(true);
    expect(isReconnectCode('connector.scope_missing')).toBe(true);
    expect(isReconnectCode('connector.upstream_timeout')).toBe(false);
    expect(isReconnectCode(null)).toBe(false);
    expect(isReconnectCode(undefined)).toBe(false);
  });

  it('非 ApiError 的失败没有错误码', () => {
    expect(errorCodeOf(new Error('boom'))).toBeNull();
    expect(isReconnectError(new Error('boom'))).toBe(false);
  });

  it('回调地址固定回 /connectors', () => {
    expect(connectorsReturnUrl('http://localhost:5173')).toBe(
      'http://localhost:5173/connectors',
    );
  });
});
