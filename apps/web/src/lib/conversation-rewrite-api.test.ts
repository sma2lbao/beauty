import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './http';
import { rewriteMessage } from './conversations-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** 假流式响应:按帧一次性给完,足够验证解析与分发(不依赖 ReadableStream 全局)。 */
function fakeStreamResponse(frames: string) {
  const chunks = [new TextEncoder().encode(frames)];
  let index = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length
            ? { done: false, value: chunks[index++] }
            : { done: true, value: undefined },
      }),
    },
  };
}

describe('rewriteMessage(interaction-closure §2.1)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('编辑重发:POST /messages/{id}/rewrite 携带 content,帧分发与发消息一致', async () => {
    const fetchMock = vi.fn(async () =>
      fakeStreamResponse(
        [
          'event: meta',
          'data: {"mode":"chat","message":{"id":"m1"},"run":null}',
          '',
          'event: delta',
          'data: {"text":"好的"}',
          '',
          'event: done',
          'data: {"mode":"chat","reply":null}',
          '',
          '',
        ].join('\n'),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const onMeta = vi.fn();
    const onDelta = vi.fn();
    const onDone = vi.fn();

    await rewriteMessage('c1', 'm1', { onMeta, onDelta, onDone }, '改过的内容');

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/conversations/c1/messages/m1/rewrite');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify({ content: '改过的内容' }));
    expect(onMeta).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'chat' }),
    );
    expect(onDelta).toHaveBeenCalledWith('好的');
    expect(onDone).toHaveBeenCalled();
  });

  it('重新生成:省略 content 时 body 为空对象(不落 content 键)', async () => {
    const fetchMock = vi.fn(async () =>
      fakeStreamResponse(
        [
          'event: done',
          'data: {"mode":"chat","reply":null}',
          '',
          '',
        ].join('\n'),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const onDone = vi.fn();

    await rewriteMessage('c1', 'm2', { onDone });

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.body).toBe(JSON.stringify({}));
    expect(init.body).not.toContain('content');
    expect(onDone).toHaveBeenCalled();
  });

  it('error 帧转成 onError(HTTP 已是 200,不改状态码)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeStreamResponse(
          [
            'event: error',
            'data: {"code":"conversation.run_active","message":"这条消息关联的任务还没结束"}',
            '',
            '',
          ].join('\n'),
        ),
      ),
    );
    const onError = vi.fn();

    await rewriteMessage('c1', 'm1', { onError }, '改过的内容');

    expect(onError).toHaveBeenCalledWith({
      code: 'conversation.run_active',
      message: '这条消息关联的任务还没结束',
    });
  });

  it('开流前 409 映射为带业务码的 ApiError(只能重写最后一轮)', async () => {
    mockFetch(409, {
      code: 'conversation.message_not_latest',
      message: '只能重写最后一轮',
    });

    const error = (await rewriteMessage('c1', 'm1', {}, '改过的内容').catch(
      (failure: unknown) => failure,
    )) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('conversation.message_not_latest');
    expect(error.message).toBe('只能重写最后一轮');
  });

  it('HTTP 200 但没有流式内容时抛 stream_failed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, body: null })),
    );

    const error = (await rewriteMessage('c1', 'm2', {}).catch(
      (failure: unknown) => failure,
    )) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('stream_failed');
    expect(error.message).toBe('响应没有流式内容');
  });
});
