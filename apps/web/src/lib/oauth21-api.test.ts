import { afterEach, describe, expect, it, vi } from 'vitest';

import { oauth21Api } from './oauth21-api';

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

describe('oauth21Api', () => {
  it('读取客户端与提交授权确认', async () => {
    const clientMock = mockFetch(200, { client_id: 'c1', client_name: 'MCP' });
    await oauth21Api.client('c1');
    expect(clientMock.mock.calls[0]?.[0]).toBe(
      '/api/account/v1/oauth/clients/c1',
    );

    const approveMock = mockFetch(200, { redirect_to: 'http://x/cb?code=1' });
    await oauth21Api.approve({
      client_id: 'c1',
      redirect_uri: 'http://x/cb',
      scope: 'mcp:tools:read',
      state: 's',
      code_challenge: 'challenge',
      code_challenge_method: 'S256',
    });
    const [url, init] = approveMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/account/v1/oauth/authorize/approve');
    expect(init.method).toBe('POST');

    const listMock = mockFetch(200, []);
    await oauth21Api.authorizations();
    expect(listMock.mock.calls[0]?.[0]).toBe(
      '/api/account/v1/oauth/authorizations',
    );

    const revokeMock = mockFetch(204);
    await oauth21Api.revokeAuthorization('c1');
    expect(revokeMock.mock.calls[0]?.[0]).toBe(
      '/api/account/v1/oauth/authorizations/c1',
    );
    expect((revokeMock.mock.calls[0]?.[1] as RequestInit).method).toBe(
      'DELETE',
    );
  });
});
