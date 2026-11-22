import { afterEach, describe, expect, it, vi } from 'vitest';

import { parseSseFrame, streamTurn } from '@/lib/conversation-stream';
import type { StreamMessageHandlers } from '@/lib/conversations-api';

const encoder = new TextEncoder();

const fallback = {
  code: 'conversation_failed',
  emptyMessage: '没有流式内容',
  failureMessage: (status: number) => `请求失败(${status})`,
};

/** 用 ReadableStream 构造一个假 SSE 响应(帧可跨分片)。 */
function streamResponse(chunks: string[], status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({}),
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('parseSseFrame', () => {
  it('解析具名帧,坏 JSON 返回 null', () => {
    expect(parseSseFrame('event: delta\ndata: {"text":"你"}')).toEqual([
      'delta',
      { text: '你' },
    ]);
    expect(parseSseFrame('event: delta\ndata: {oops')).toBeNull();
  });
});

describe('streamTurn', () => {
  it('收到 done 后连接结束属正常收尾', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          streamResponse([
            'event: meta\ndata: {"mode":"chat"}\n\n',
            'event: delta\ndata: {"text":"你好"}\n\n',
            'event: done\ndata: {"mode":"chat","reply":null}\n\n',
          ]),
        ),
    );
    const onDone = vi.fn();
    const onError = vi.fn();
    const handlers: StreamMessageHandlers = { onDone, onError };

    await expect(
      streamTurn(
        '/api/v1/conversations/c1/messages/stream',
        '{}',
        handlers,
        fallback,
      ),
    ).resolves.toBeUndefined();
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it('没收完就断(没有 done/error):抛 stream.interrupted', async () => {
    // 对话流不发 stream.end,服务端发完 done 直接关连接;
    // 因此"读到 EOF 却没见到终态帧"只能判定为断线。
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          streamResponse([
            'event: meta\ndata: {"mode":"chat"}\n\n',
            'event: delta\ndata: {"text":"写到一半"}\n\n',
          ]),
        ),
    );
    const onDelta = vi.fn();

    await expect(
      streamTurn(
        '/api/v1/conversations/c1/messages/stream',
        '{}',
        { onDelta },
        fallback,
      ),
    ).rejects.toMatchObject({
      code: 'stream.interrupted',
      message: '回复中断,已保存部分内容,可重新生成',
    });
    expect(onDelta).toHaveBeenCalledWith('写到一半');
  });

  it('error 帧是终态:连接随后结束不误报中断', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          streamResponse([
            'event: error\ndata: {"code":"quota.monthly_tokens_exceeded","message":"超配额"}\n\n',
          ]),
        ),
    );
    const onError = vi.fn();

    await expect(
      streamTurn('/x', '{}', { onError }, fallback),
    ).resolves.toBeUndefined();
    expect(onError).toHaveBeenCalledWith({
      code: 'quota.monthly_tokens_exceeded',
      message: '超配额',
    });
  });

  it('开流前 4xx:按统一错误契约抛 ApiError(不误判中断)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({
          code: 'conversation.run_active',
          message: '任务还在跑',
        }),
      }),
    );

    await expect(streamTurn('/x', '{}', {}, fallback)).rejects.toMatchObject({
      status: 409,
      code: 'conversation.run_active',
      message: '任务还在跑',
    });
  });
});
