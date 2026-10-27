/**
 * 会话级工具调用审计的契约:端点路径/分页参数、snake→camel 归一、
 * 服务端补丁按文件筛选与时间排序、降级语义、增删行数聚合。
 *
 * 零网络:fetch 一律打桩。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setStoredToken } from './auth';
import {
  fileLineStats,
  filePatchesFromCalls,
  listConversationToolCalls,
  useConversationToolCalls,
} from './tool-calls-api';
import type { ConversationToolCallPage } from './tool-calls-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function page(
  overrides: Partial<ConversationToolCallPage> = {},
): ConversationToolCallPage {
  return {
    items: [],
    total: 0,
    limit: 200,
    offset: 0,
    degraded: false,
    ...overrides,
  };
}

/** 一条 write/edit 审计记录的最小形态(列表层已归一成 camelCase)。 */
function call(extra: Partial<ConversationToolCallPage['items'][number]>) {
  return {
    id: 'tc-1',
    toolCallId: 'call-1',
    runId: null,
    conversationId: 'conv-1',
    toolName: 'write',
    toolVersion: null,
    risk: null,
    decision: null,
    ok: true,
    errorCode: null,
    durationMs: null,
    resultChars: null,
    bytesWritten: null,
    truncated: false,
    sha256Before: null,
    sha256After: null,
    paths: ['/workspace/src/a.ts'],
    patch: '@@ -0,0 +1 @@\n+const a = 1;',
    linesAdded: 1,
    linesRemoved: 0,
    patchTruncated: false,
    sandboxId: null,
    createdAt: '2026-10-01T00:00:00Z',
    ...extra,
  };
}

/** 后端原样返回的 snake_case 记录(HTTP 契约测试用)。 */
function rawCall(extra: Record<string, unknown>) {
  return {
    id: 'tc-1',
    tool_call_id: 'call-1',
    tool_name: 'write',
    ok: true,
    paths: ['/workspace/src/a.ts'],
    patch: '@@ -0,0 +1 @@\n+const a = 1;',
    lines_added: 1,
    lines_removed: 0,
    created_at: '2026-10-01T00:00:00Z',
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('listConversationToolCalls', () => {
  it('走同源 /api、带默认分页参数,并把响应归一成 camelCase', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, {
      items: [
        rawCall({
          run_id: 'run-1',
          conversation_id: 'conv-1',
          tool_version: '1.0',
          risk: 'write',
          decision: 'auto',
          error_code: null,
          duration_ms: 12,
          result_chars: 34,
          bytes_written: 12,
          truncated: false,
          sha256_before: null,
          sha256_after: 'abc',
          lines_added: 2,
          lines_removed: 1,
          patch_truncated: true,
          sandbox_id: 'sb-1',
        }),
      ],
      total: 1,
      limit: 200,
      offset: 0,
      degraded: false,
    });

    const result = await listConversationToolCalls('conv-1');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/conversations/conv-1/tool-calls?limit=200&offset=0');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-jwt',
    });
    expect(result.degraded).toBe(false);
    expect(result.total).toBe(1);
    expect(result.items[0]).toMatchObject({
      id: 'tc-1',
      toolCallId: 'call-1',
      runId: 'run-1',
      conversationId: 'conv-1',
      toolName: 'write',
      durationMs: 12,
      resultChars: 34,
      bytesWritten: 12,
      sha256After: 'abc',
      linesAdded: 2,
      linesRemoved: 1,
      patchTruncated: true,
      sandboxId: 'sb-1',
      createdAt: '2026-10-01T00:00:00Z',
    });
    expect(result.items[0].paths).toEqual(['/workspace/src/a.ts']);
  });

  it('自定义 limit/offset 进查询串,会话 id 做转义', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, page());

    await listConversationToolCalls('conv/1', { limit: 50, offset: 100 });

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/v1/conversations/conv%2F1/tool-calls?limit=50&offset=100',
    );
  });

  it('缺字段时降级成 null/空数组,不猜', async () => {
    setStoredToken('test-jwt');
    mockFetch(200, { items: [{ toolName: 'read' }] });

    const result = await listConversationToolCalls('conv-1');

    expect(result).toMatchObject({ total: 0, limit: 200, offset: 0, degraded: false });
    expect(result.items[0]).toMatchObject({
      id: '',
      toolCallId: '',
      runId: null,
      paths: [],
      patch: null,
      linesAdded: null,
      linesRemoved: null,
      ok: true,
      truncated: false,
      patchTruncated: false,
      createdAt: '',
    });
  });
});

describe('filePatchesFromCalls', () => {
  it('只取命中该文件的 write/edit 且 patch 非空,按时间升序', () => {
    const later = call({
      id: 'tc-2',
      toolName: 'edit',
      createdAt: '2026-10-01T02:00:00Z',
      patch: '@@ -1 +1 @@\n-const a = 1;\n+const a = 2;',
    });
    const earlier = call({ id: 'tc-1', createdAt: '2026-10-01T01:00:00Z' });
    const otherFile = call({ id: 'tc-3', paths: ['/workspace/src/b.ts'] });
    const noPatch = call({ id: 'tc-4', patch: null });
    const readOnly = call({ id: 'tc-5', toolName: 'read' });

    const result = filePatchesFromCalls(
      page({ items: [later, earlier, otherFile, noPatch, readOnly] as never }),
      '/workspace/src/a.ts',
    );

    expect(result.degraded).toBe(false);
    expect(result.items.map((item) => item.patch)).toEqual([
      '@@ -0,0 +1 @@\n+const a = 1;',
      '@@ -1 +1 @@\n-const a = 1;\n+const a = 2;',
    ]);
    expect(result.items[0]).toMatchObject({
      toolName: 'write',
      path: '/workspace/src/a.ts',
      linesAdded: 1,
      linesRemoved: 0,
      patchTruncated: false,
      createdAt: '2026-10-01T01:00:00Z',
    });
  });

  it('相对路径也能命中同一个文件', () => {
    const result = filePatchesFromCalls(
      page({ items: [call({ paths: ['src/a.ts'] })] as never }),
      '/workspace/src/a.ts',
    );

    expect(result.items).toHaveLength(1);
    expect(result.items[0].path).toBe('/workspace/src/a.ts');
  });

  it('降级时空结果并透出 degraded,让调用方回退本地重建', () => {
    const result = filePatchesFromCalls(
      page({ items: [call({})] as never, degraded: true }),
      '/workspace/src/a.ts',
    );

    expect(result).toEqual({ items: [], degraded: true });
    expect(filePatchesFromCalls(undefined, '/workspace/src/a.ts')).toEqual({
      items: [],
      degraded: false,
    });
  });
});

describe('fileLineStats', () => {
  it('按文件相对路径累加 write/edit 的增删行数,跳过无行数与只读调用', () => {
    const stats = fileLineStats(
      page({
        items: [
          call({ linesAdded: 3, linesRemoved: 1 }),
          call({
            id: 'tc-2',
            toolName: 'edit',
            linesAdded: 2,
            linesRemoved: 4,
            paths: ['/workspace/src/a.ts', '/workspace/src/b.ts'],
          }),
          call({ id: 'tc-3', toolName: 'read', linesAdded: 9, linesRemoved: 9 }),
          call({ id: 'tc-4', linesAdded: null, linesRemoved: null }),
        ] as never,
      }),
    );

    expect(stats.get('src/a.ts')).toEqual({ added: 5, removed: 5 });
    expect(stats.get('src/b.ts')).toEqual({ added: 2, removed: 4 });
    expect(stats.size).toBe(2);
  });

  it('降级时返回空表', () => {
    expect(
      fileLineStats(page({ items: [call({})] as never, degraded: true })).size,
    ).toBe(0);
  });
});

describe('useConversationToolCalls', () => {
  function wrapper(client: QueryClient) {
    return function Wrapper({ children }: { children: ReactNode }) {
      return createElement(QueryClientProvider, { client }, children);
    };
  }

  it('没有会话 id 时不发请求', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, page());
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useConversationToolCalls(undefined), {
      wrapper: wrapper(client),
    });

    await waitFor(() => expect(result.current.isPending).toBe(true));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('有 id 时按会话拉取', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, page({ total: 2 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useConversationToolCalls('conv-1'), {
      wrapper: wrapper(client),
    });

    await waitFor(() => expect(result.current.data?.total).toBe(2));
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/v1/conversations/conv-1/tool-calls?limit=200&offset=0',
    );
  });
});