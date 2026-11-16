import { afterEach, describe, expect, it, vi } from 'vitest';

import { notificationsApi, streamNotifications } from './notifications-api';

const NEWLINE = String.fromCharCode(10);

function fakeBody(chunks: string[]) {
  let index = 0;
  return {
    getReader: () => ({
      read: async () =>
        index < chunks.length
          ? { done: false, value: new TextEncoder().encode(chunks[index++]) }
          : { done: true, value: undefined },
    }),
  };
}

function jsonResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload };
}

const notification = {
  id: 'n1',
  kind: 'run.status',
  title: '任务已完成',
  body: '整理周报',
  tone: 'success',
  to_path: '/runs',
  run_id: 'r1',
  conversation_id: null,
  payload: { run_id: 'r1' },
  created_at: '2026-11-16T12:00:00+00:00',
  read_at: null,
};

describe('notificationsApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('list 带上 limit 与 before 游标', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [notification],
        unread_count: 1,
        next_cursor: null,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const page = await notificationsApi.list({ limit: 5, before: 'c1' });

    expect(page.unread_count).toBe(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/notifications?limit=5&before=c1');
  });

  it('markRead / markAllRead 用 POST 回写服务端已读', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ...notification, read_at: 'now' }));
    vi.stubGlobal('fetch', fetchMock);

    await notificationsApi.markRead('n1');
    await notificationsApi.markAllRead();

    const calls = fetchMock.mock.calls as [string, RequestInit][];
    expect(calls[0][0]).toBe('/api/v1/notifications/n1/read');
    expect(calls[1][0]).toBe('/api/v1/notifications/read-all');
    expect(calls[0][1].method).toBe('POST');
    expect(calls[1][1].method).toBe('POST');
  });
});

describe('streamNotifications', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('订阅时声明 Accept: text/event-stream(API 网关据此流式转发,否则缓冲到读超时)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 500, body: null });
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      streamNotifications({ onNotification: () => undefined }),
    ).rejects.toThrow('通知流连接失败(500)');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Accept).toBe(
      'text/event-stream',
    );
    expect(init.credentials).toBe('include');
  });

  it('解析 notification.v1 帧并逐条回调(载荷是服务端通知行)', async () => {
    const data = JSON.stringify(notification);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: fakeBody([
        ': ready' + NEWLINE + NEWLINE,
        'id: c1|n1' +
          NEWLINE +
          'event: notification.v1' +
          NEWLINE +
          'data: ' +
          data +
          NEWLINE +
          NEWLINE,
      ]),
    });
    vi.stubGlobal('fetch', fetchMock);
    const onOpen = vi.fn();
    const got: unknown[] = [];

    await streamNotifications({ onOpen, onNotification: (n) => got.push(n) });

    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(got).toHaveLength(1);
    expect(got[0]).toMatchObject({
      id: 'n1',
      title: '任务已完成',
      read_at: null,
    });
  });
});
