import { afterEach, describe, expect, it, vi } from 'vitest';

import { streamNotifications } from './notifications-api';

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

describe('streamNotifications', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('订阅时声明 Accept: text/event-stream(BFF 据此流式转发,否则缓冲到读超时)', async () => {
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

  it('解析 run.status_changed 帧并逐条回调', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: fakeBody([
        ': ready\n\n',
        'id: 1\nevent: run.status_changed.v1\ndata: {"run_id":"r1","conversation_id":"c1","title":"周报","goal":"汇总","status":"completed","updated_at":null}\n\n',
      ]),
    });
    vi.stubGlobal('fetch', fetchMock);
    const onOpen = vi.fn();
    const got: unknown[] = [];

    await streamNotifications({ onOpen, onNotification: (n) => got.push(n) });

    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(got).toHaveLength(1);
    expect(got[0]).toMatchObject({ run_id: 'r1', status: 'completed' });
  });
});
