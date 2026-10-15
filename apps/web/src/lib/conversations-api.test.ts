import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './http';
import { conversationsApi } from './conversations-api';

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

describe('conversationsApi', () => {
  it('列表走 /api/v1/conversations?limit=50', async () => {
    const fetchMock = mockFetch(200, []);

    await conversationsApi.list();

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/conversations?limit=50');
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

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/conversations/c1/messages');
  });

  it('postMessage 发 JSON content + mode,默认 auto', async () => {
    const result = {
      message: {
        id: 'm1',
        conversation_id: 'c1',
        run_id: 'r1',
        role: 'user',
        content: '把本周邮件整理成周报',
        created_at: '2026-10-15T10:00:00+00:00',
      },
      run: { id: 'r1' },
      reply: null,
      mode: 'run_created',
    };
    const fetchMock = mockFetch(201, result);

    await expect(
      conversationsApi.postMessage('c1', '把本周邮件整理成周报'),
    ).resolves.toEqual(result);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/conversations/c1/messages');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(
      JSON.stringify({ content: '把本周邮件整理成周报', mode: 'auto' }),
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
    expect(init.body).toBe(JSON.stringify({ content: '你好', mode: 'chat' }));
  });

  it('跨租户探测返回 404 conversation.not_found 映射为 ApiError', async () => {
    mockFetch(404, { code: 'conversation.not_found', message: '对话不存在' });

    const error = (await conversationsApi.get('nope').catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.code).toBe('conversation.not_found');
  });
});
