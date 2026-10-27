/**
 * 文件树的会话级增删行数:文件行显示 +N/−M,目录行显示子树汇总;
 * 没有会话 id 或端点降级时保持原来的纯文件树(不发多余请求)。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SandboxFileTree } from './sandbox-file-tree';

const TREE = {
  path: '/workspace',
  truncated: false,
  entries: [
    { path: '/workspace/src', name: 'src', kind: 'dir', size_bytes: null },
    { path: '/workspace/src/a.ts', name: 'a.ts', kind: 'file', size_bytes: 12 },
    { path: '/workspace/src/b.ts', name: 'b.ts', kind: 'file', size_bytes: 34 },
    { path: '/workspace/README.md', name: 'README.md', kind: 'file', size_bytes: 56 },
  ],
};

function toolCall(extra: Record<string, unknown>) {
  return {
    id: 'tc-1',
    tool_call_id: 'call-1',
    tool_name: 'write',
    ok: true,
    paths: ['/workspace/src/a.ts'],
    patch: '@@ -0,0 +1 @@\n+x',
    lines_added: 1,
    lines_removed: 0,
    created_at: '2026-10-01T00:00:00Z',
    ...extra,
  };
}

/** 按 URL 分流:tree 请求与 tool-calls 请求。 */
function mockFetch(toolCallsBody: unknown) {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    const body = url.includes('/tool-calls') ? toolCallsBody : TREE;
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderTree(
  overrides: Partial<{
    conversationId: string | undefined;
    touchedPaths: string[];
  }> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SandboxFileTree
        sandboxId="sb-1"
        touchedPaths={overrides.touchedPaths}
        conversationId={overrides.conversationId}
        onSelect={() => undefined}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('SandboxFileTree 增删行数', () => {
  it('文件行显示本会话 +N/−M,目录行显示子树汇总', async () => {
    mockFetch({
      items: [
        toolCall({ lines_added: 3, lines_removed: 1 }),
        toolCall({
          id: 'tc-2',
          tool_name: 'edit',
          paths: ['/workspace/src/b.ts'],
          lines_added: 2,
          lines_removed: 1,
        }),
      ],
      total: 2,
      limit: 200,
      offset: 0,
      degraded: false,
    });

    renderTree({ conversationId: 'conv-1' });

    // 目录行(折叠状态也显示子树汇总):src 子树合计 +5 −2。
    const srcRow = await screen.findByTitle('本会话 +5 −2 行');
    expect(srcRow.textContent).toContain('+5');

    // 展开 src 后,文件行显示各自的增删行数。
    fireEvent.click(screen.getByText('src'));
    expect(await screen.findByText('a.ts')).toBeTruthy();
    expect(screen.getAllByTitle('本会话 +3 −1 行')).toHaveLength(1);
    expect(screen.getAllByTitle('本会话 +2 −1 行')).toHaveLength(1);
    expect(screen.getByText('+3')).toBeTruthy();
    expect(screen.getAllByText('−1').length).toBeGreaterThan(0);
  });

  it('没有会话 id 时不请求 tool-calls,也不显示行数', async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 200, offset: 0, degraded: false });

    renderTree();

    expect(await screen.findByText('src')).toBeTruthy();
    expect(screen.queryByText('+3')).toBeNull();
    expect(
      fetchMock.mock.calls.every(([url]) => !String(url).includes('/tool-calls')),
    ).toBe(true);
  });

  it('端点降级时保持现状(不显示行数)', async () => {
    mockFetch({
      items: [toolCall({ lines_added: 3, lines_removed: 1 })],
      total: 1,
      limit: 200,
      offset: 0,
      degraded: true,
    });

    renderTree({ conversationId: 'conv-1' });

    expect(await screen.findByText('src')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('+3')).toBeNull());
  });

  it('触碰圆点与行数可以同时出现', async () => {
    mockFetch({
      items: [toolCall({ lines_added: 3, lines_removed: 1 })],
      total: 1,
      limit: 200,
      offset: 0,
      degraded: false,
    });

    renderTree({ conversationId: 'conv-1', touchedPaths: ['src/a.ts'] });

    // 触碰过的目录/文件都带圆点(折叠状态下目录先亮)。
    expect(
      (await screen.findAllByLabelText('本会话工具调用触碰过')).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByText('src'));
    // 只有 a.ts 一条改动时,src 目录汇总与 a.ts 自身同为 +3 −1(目录行 + 文件行)。
    expect(await screen.findAllByTitle('本会话 +3 −1 行')).toHaveLength(2);
    expect(screen.getAllByText('+3').length).toBeGreaterThan(0);
  });
});