import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './http';
import {
  conversationsApi,
  parseSseFrame,
  streamMessage,
} from './conversations-api';

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
});

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

describe('conversationsApi', () => {
  it('列表走 /api/v1/conversations?limit=50 并解包 items', async () => {
    const fetchMock = mockFetch(200, {
      items: [{ id: 'c1' }],
      next_cursor: null,
    });

    const items = await conversationsApi.list();

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/conversations?limit=50');
    expect(items).toEqual([{ id: 'c1' }]);
  });

  it('分页列表带 q / archived / cursor', async () => {
    const fetchMock = mockFetch(200, { items: [], next_cursor: null });

    await conversationsApi.page({
      limit: 10,
      q: '周报',
      archived: true,
      cursor: 'abc',
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/conversations?limit=10&q=%E5%91%A8%E6%8A%A5&archived=true&cursor=abc',
    );
  });

  it('兼容旧版后端的数组响应(滚动升级不崩)', async () => {
    mockFetch(200, [{ id: 'c1' }]);

    const page = await conversationsApi.page();

    expect(page).toEqual({ items: [{ id: 'c1' }], next_cursor: null });
  });

  it('重命名走 PATCH,删除走 DELETE', async () => {
    const fetchMock = mockFetch(200, { id: 'c1', title: '新标题' });

    await conversationsApi.update('c1', { title: '新标题' });
    const [updateUrl, updateInit] = fetchMock.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(updateUrl).toBe('/api/v1/conversations/c1');
    expect(updateInit.method).toBe('PATCH');
    expect(updateInit.body).toBe(JSON.stringify({ title: '新标题' }));

    const removeMock = mockFetch(204);
    await conversationsApi.remove('c1');
    const [removeUrl, removeInit] = removeMock.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(removeUrl).toBe('/api/v1/conversations/c1');
    expect(removeInit.method).toBe('DELETE');
  });

  it('create 用 POST 空对象(标题由首条消息自动定题)', async () => {
    const fetchMock = mockFetch(201, { id: 'c1', title: '新对话' });

    await conversationsApi.create();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/conversations');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify({}));
  });

  it('get 按会话 id 取详情', async () => {
    const fetchMock = mockFetch(200, { id: 'c1' });

    await conversationsApi.get('c1');

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/conversations/c1');
  });

  it('messages 回放该会话的消息', async () => {
    const fetchMock = mockFetch(200, []);

    await conversationsApi.messages('c1');

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/conversations/c1/messages',
    );
  });

  it('postMessage 发 JSON content + mode,默认 auto', async () => {
    const result = {
      message: {
        id: 'm1',
        conversation_id: 'c1',
        run_id: 'r1',
        role: 'user',
        content: '把本周日程整理成周报',
        created_at: '2026-10-15T10:00:00+00:00',
      },
      run: { id: 'r1' },
      reply: null,
      mode: 'run_created',
    };
    const fetchMock = mockFetch(201, result);

    await expect(
      conversationsApi.postMessage('c1', '把本周日程整理成周报'),
    ).resolves.toEqual(result);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/conversations/c1/messages');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(
      JSON.stringify({
        content: '把本周日程整理成周报',
        mode: 'auto',
        attachment_ids: [],
      }),
    );
  });

  it('postMessage 可指定发送方式(chat)', async () => {
    const fetchMock = mockFetch(201, {
      message: { id: 'm1' },
      run: null,
      reply: { id: 'a1', content: '你好' },
      mode: 'chat',
    });

    await conversationsApi.postMessage('c1', '你好', 'chat');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBe(
      JSON.stringify({ content: '你好', mode: 'chat', attachment_ids: [] }),
    );
  });

  it('parseSseFrame 解析具名帧,坏帧返回 null', () => {
    expect(parseSseFrame('event: delta\ndata: {"text":"你好"}')).toEqual([
      'delta',
      { text: '你好' },
    ]);
    expect(parseSseFrame('data: {"text":"x"}')).toBeNull(); // 缺事件名
    expect(parseSseFrame('event: delta\ndata: 不是 JSON')).toBeNull();
    expect(parseSseFrame(': keep-alive')).toBeNull();
  });

  it('streamMessage 按事件名分发 meta/delta/done', async () => {
    const frames =
      'event: meta\ndata: {"mode":"chat","message":{"id":"m1"},"run":null}\n\n' +
      'event: delta\ndata: {"text":"你"}\n\n' +
      'event: delta\ndata: {"text":"好"}\n\n' +
      'event: done\ndata: {"mode":"chat","reply":null}\n\n';
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      fakeStreamResponse(frames),
    );
    vi.stubGlobal('fetch', fetchMock);
    const deltas: string[] = [];
    let meta: unknown;
    let done: unknown;

    await streamMessage('c1', '你好', 'auto', {
      onMeta: (value) => {
        meta = value;
      },
      onDelta: (text) => deltas.push(text),
      onDone: (value) => {
        done = value;
      },
    });

    expect(meta).toEqual({ mode: 'chat', message: { id: 'm1' }, run: null });
    expect(deltas).toEqual(['你', '好']); // 增量按到达顺序回调
    expect(done).toEqual({ mode: 'chat', reply: null });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/conversations/c1/messages/stream');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(
      JSON.stringify({ content: '你好', mode: 'auto', attachment_ids: [] }),
    );
  });

  it('streamMessage 把 error 帧交给 onError', async () => {
    const frames =
      'event: meta\ndata: {"mode":"chat","message":{"id":"m1"},"run":null}\n\n' +
      'event: error\ndata: {"code":"llm.upstream_error","message":"上游断了"}\n\n';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeStreamResponse(frames)),
    );
    const errors: unknown[] = [];

    await streamMessage('c1', '你好', 'chat', {
      onError: (value) => errors.push(value),
    });

    expect(errors).toEqual([
      { code: 'llm.upstream_error', message: '上游断了' },
    ]);
  });

  it('streamMessage 开流前的错误仍抛 ApiError(chat 未启用模型)', async () => {
    mockFetch(409, { code: 'chat.llm_disabled', message: '未启用模型' });

    const error = (await streamMessage('c1', '你好', 'chat', {}).catch(
      (failure: unknown) => failure,
    )) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('chat.llm_disabled');
  });
  it('跨租户探测返回 404 conversation.not_found 映射为 ApiError', async () => {
    mockFetch(404, { code: 'conversation.not_found', message: '对话不存在' });

    const error = (await conversationsApi
      .get('nope')
      .catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.code).toBe('conversation.not_found');
  });
});

describe('attachments API', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('列表与上传走会话附件路由', async () => {
    const fetchMock = mockFetch(200, []);
    await conversationsApi.attachments('c1');
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/conversations/c1/attachments',
    );

    const uploadMock = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ id: 'a1', filename: 'note.md' }),
    }));
    vi.stubGlobal('fetch', uploadMock);
    const file = new File(['# hello'], 'note.md', { type: 'text/markdown' });
    const attachment = await conversationsApi.uploadAttachment('c1', file);

    expect(attachment).toMatchObject({ id: 'a1', filename: 'note.md' });
    const [url, init] = uploadMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/conversations/c1/attachments');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
  });
});
