import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectorApi } from './connector-api';

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

describe('connectorApi', () => {
  it('列表/测试/最近邮件走 /api/connector', async () => {
    const listMock = mockFetch(200, []);
    await connectorApi.list();
    expect(listMock.mock.calls[0]?.[0]).toBe('/api/connector/connections');

    const testMock = mockFetch(200, { ok: true, detail: 'ok' });
    await connectorApi.test('c1');
    expect(testMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/test',
    );
    expect((testMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');

    const providersMock = mockFetch(200, []);
    await connectorApi.oauthProviders();
    expect(providersMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/oauth/providers',
    );

    const startMock = mockFetch(200, { authorize_url: 'https://x' });
    await connectorApi.startOAuth('google');
    expect(startMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/oauth/google/start',
    );

    const emailsMock = mockFetch(200, []);
    await connectorApi.emails('c1', 5);
    expect(emailsMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/emails?limit=5',
    );

    const attachmentsMock = mockFetch(200, []);
    await connectorApi.attachments('c1', 'm1');
    expect(attachmentsMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/emails/m1/attachments',
    );

    const eventsMock = mockFetch(200, []);
    await connectorApi.events('c1');
    expect(eventsMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/events',
    );
  });

  it('创建提交凭证到后端,发信是 POST', async () => {
    const createMock = mockFetch(201, { id: 'c1' });
    await connectorApi.create({
      provider: 'imap',
      account: 'a@b.c',
      credentials: { host: 'imap', username: 'a@b.c', password: 'p' },
    });
    const [url, init] = createMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/connector/connections');
    expect(init.method).toBe('POST');
    expect(init.body).toContain('"password":"p"');

    const sendMock = mockFetch(200, { ok: true, detail: 'sent' });
    await connectorApi.sendEmail('c1', {
      to: 'x@y.z',
      subject: 's',
      body: 'b',
    });
    expect(sendMock.mock.calls[0]?.[0]).toBe(
      '/api/connector/connections/c1/emails',
    );
  });
});
