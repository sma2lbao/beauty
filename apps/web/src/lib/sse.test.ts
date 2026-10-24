import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import {
  connectSse,
  parseSseFrameText,
  readSseStream,
  type SseFrame,
} from '@/lib/sse';

const encoder = new TextEncoder();

/** 用 ReadableStream 按分片构造一个假 SSE 响应(模拟真实网络分片)。 */
function streamResponse(chunks: string[], status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
  } as unknown as Response;
}

/** 跑一次 readSseStream,收集帧与收尾/中断回调。 */
function collect(chunks: string[]) {
  const frames: SseFrame[] = [];
  const onEnd = vi.fn();
  const onInterrupted = vi.fn();
  const run = readSseStream(streamResponse(chunks), {
    onFrame: (frame) => frames.push(frame),
    onEnd,
    onInterrupted,
  });
  return { run, frames, onEnd, onInterrupted };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('parseSseFrameText', () => {
  it('解析 event/data,多行 data 按换行拼接,保留原始帧', () => {
    const frame = parseSseFrameText(
      'id: 9\nevent: delta\ndata: 第一行\ndata: 第二行',
    );

    expect(frame).toMatchObject({ event: 'delta', data: '第一行\n第二行' });
    expect(frame?.raw).toContain('event: delta');
  });

  it('注释/空帧返回 null,注释行不污染 data', () => {
    expect(parseSseFrameText(': keep-alive')).toBeNull();
    expect(parseSseFrameText(': ping\ndata: {"ok":true}')).toMatchObject({
      event: null,
      data: '{"ok":true}',
    });
    expect(
      parseSseFrameText('event: x\ndata: a\n: comment\ndata: b'),
    ).toMatchObject({ event: 'x', data: 'a\nb' });
  });
});

describe('readSseStream', () => {
  it('多帧逐条回调(一帧一片)', async () => {
    const { run, frames, onEnd } = collect([
      'id: 1\nevent: meta\ndata: {"mode":"chat"}\n\n',
      'id: 2\nevent: delta\ndata: {"text":"你"}\n\n',
    ]);

    await run;

    expect(frames.map((frame) => frame.event)).toEqual(['meta', 'delta']);
    expect(frames[1].data).toBe('{"text":"你"}');
    expect(onEnd).not.toHaveBeenCalled();
  });

  it('跨分片:帧被 chunk 边界切开也能拼齐', async () => {
    const { run, frames } = collect([
      'event: delta\ndata: {"text":"你',
      '好"}\n\nevent: delta\ndata: {"text":"!"}',
      '\n\n',
    ]);

    await run;

    expect(frames).toHaveLength(2);
    expect(JSON.parse(frames[0].data)).toEqual({ text: '你好' });
    expect(JSON.parse(frames[1].data)).toEqual({ text: '!' });
  });

  it('注释行(心跳)跳过,不产生帧', async () => {
    const { run, frames } = collect([
      ': keep-alive\n\n',
      'event: note\ndata: {"ok":true}\n\n',
    ]);

    await run;

    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe('note');
  });

  it('stream.end 收尾:onEnd 一次,后续帧不再回调', async () => {
    const { run, frames, onEnd, onInterrupted } = collect([
      'event: run.completed.v1\ndata: {"seq":1}\n\n',
      'event: stream.end\ndata: {"status":"completed"}\n\n',
      'event: ghost\ndata: {}\n\n',
    ]);

    await run;

    expect(frames.map((frame) => frame.event)).toEqual(['run.completed.v1']);
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(onInterrupted).not.toHaveBeenCalled();
  });

  it('没有 stream.end 就 EOF:onInterrupted(不抛)', async () => {
    const { run, onInterrupted } = collect([
      'event: delta\ndata: {"text":"x"}\n\n',
    ]);

    await run;

    expect(onInterrupted).toHaveBeenCalledTimes(1);
  });

  it('中断回调抛错时透传(调用方据此带游标重连)', async () => {
    await expect(
      readSseStream(streamResponse(['event: delta\ndata: {}\n\n']), {
        onInterrupted: () => {
          throw new ApiError(0, 'stream.interrupted', '事件流意外中断');
        },
      }),
    ).rejects.toMatchObject({ code: 'stream.interrupted' });
  });

  it('没有响应体:按统一错误契约抛 ApiError', async () => {
    await expect(
      readSseStream({ status: 200, body: null } as unknown as Response),
    ).rejects.toMatchObject({ code: 'stream_failed' });
  });
});

describe('connectSse', () => {
  it('带 Accept: text/event-stream / credentials / Last-Event-ID', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(streamResponse(['event: stream.end\ndata: {}\n\n']));
    vi.stubGlobal('fetch', fetchMock);
    const onOpen = vi.fn();

    await connectSse('/api/v1/runs/r1/events/stream', {
      lastEventId: 7,
      onOpen,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe('/api/v1/runs/r1/events/stream');
    expect(init.credentials).toBe('include');
    expect(headers.Accept).toBe('text/event-stream');
    expect(headers['Last-Event-ID']).toBe('7');
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('POST 请求体/自定义头透传(会话流式发消息)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(streamResponse(['event: delta\ndata: {}\n\n']));
    vi.stubGlobal('fetch', fetchMock);
    const received: string[] = [];

    await connectSse('/api/v1/conversations/c1/messages/stream', {
      method: 'POST',
      body: JSON.stringify({ content: '你好' }),
      headers: { 'X-Test': '1' },
      onFrame: (frame) => received.push(frame.event ?? ''),
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify({ content: '你好' }));
    expect(headers['X-Test']).toBe('1');
    expect(received).toEqual(['delta']);
  });

  it('连接失败:调用方 mapError 决定 code/文案', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 502, body: null }),
    );

    await expect(
      connectSse('/x', {
        mapError: (response) =>
          new ApiError(
            response.status,
            'notifications.stream_failed',
            `通知流连接失败(${response.status})`,
          ),
      }),
    ).rejects.toMatchObject({
      status: 502,
      code: 'notifications.stream_failed',
      message: '通知流连接失败(502)',
    });
  });

  it('没给 mapError 时回退 stream_failed 通用文案', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, body: null }),
    );

    await expect(connectSse('/x')).rejects.toMatchObject({
      status: 200,
      code: 'stream_failed',
      message: '流式连接失败(200)',
    });
  });
});
