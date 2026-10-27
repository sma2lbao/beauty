/**
 * 文件查看器契约:内容页读沙箱文件、改动页从工具参数重建补丁、
 * 没有沙箱/二进制文件都给出实话实说的提示。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SandboxInfo } from '@/lib/sandbox-api';
import type { ToolCall } from '@/lib/presentation';

import { SandboxFileViewer } from './sandbox-file-viewer';

const sandbox: SandboxInfo = {
  id: 'sb-1',
  name: 'conv-1',
  status: 'running',
  scopeKey: 'conv:conv-1',
  workspaceId: 'ws-1',
  runId: null,
  createdAt: '2026-10-01T00:00:00Z',
  lastUsedAt: null,
  expiresAt: null,
  lastError: null,
};

function mockFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderViewer(
  overrides: Partial<{
    sandbox: SandboxInfo | null;
    sandboxLoading: boolean;
    tab: 'content' | 'diff';
    toolCalls: ToolCall[];
    path: string;
    conversationId: string;
    onClose: () => void;
    onTabChange: (tab: 'content' | 'diff') => void;
  }> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <SandboxFileViewer
        sandbox={overrides.sandbox === undefined ? sandbox : overrides.sandbox}
        sandboxLoading={overrides.sandboxLoading ?? false}
        path={overrides.path ?? '/workspace/src/a.ts'}
        tab={overrides.tab ?? 'content'}
        onTabChange={overrides.onTabChange ?? (() => undefined)}
        toolCalls={overrides.toolCalls ?? []}
        conversationId={overrides.conversationId}
        onClose={overrides.onClose ?? (() => undefined)}
      />
    </QueryClientProvider>,
  );
  return { ...view, client };
}

/** 会话级 tool-calls 页(服务端补丁的来源)。 */
function toolCallsPage(
  items: Record<string, unknown>[],
  degraded = false,
): Record<string, unknown> {
  return { items, total: items.length, limit: 200, offset: 0, degraded };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('SandboxFileViewer', () => {
  it('内容页读取沙箱文件并展示正文', async () => {
    const fetchMock = mockFetch({
      path: '/workspace/src/a.ts',
      content_base64: btoa('const a = 1;'),
      size_bytes: 12,
    });

    renderViewer();

    expect(await screen.findByText('const a = 1;')).toBeTruthy();
    expect(screen.getByText('a.ts')).toBeTruthy();
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[0]).toBe(
      '/workspace-api/v1/sandboxes/sb-1/files?path=%2Fworkspace%2Fsrc%2Fa.ts',
    );
  });

  it('二进制文件只给提示,不渲染正文', async () => {
    mockFetch({
      path: '/workspace/logo.png',
      content_base64: btoa('a\u0000b'),
      size_bytes: 2048,
    });

    renderViewer({ path: '/workspace/logo.png' });

    expect(await screen.findByText(/这是二进制文件/)).toBeTruthy();
    expect(screen.getByText(/2\.0 KB/)).toBeTruthy();
  });

  it('没有沙箱时内容页给出提示', () => {
    renderViewer({ sandbox: null });

    expect(
      screen.getByText('这个会话还没有运行中的沙箱，暂时读不到文件内容。'),
    ).toBeTruthy();
  });

  it('沙箱查询失败时给错误与重试,而不是假装没有沙箱', () => {
    const onRetrySandbox = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <SandboxFileViewer
          sandbox={null}
          sandboxLoading={false}
          sandboxError={new Error('workspace-service 不可用')}
          onRetrySandbox={onRetrySandbox}
          path="/workspace/src/a.ts"
          tab="content"
          onTabChange={() => undefined}
          toolCalls={[]}
          onClose={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText('workspace-service 不可用')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetrySandbox).toHaveBeenCalled();
  });

  it('改动页从 write 参数重建补丁并标注来源', () => {
    const toolCalls: ToolCall[] = [
      {
        seq: 4,
        name: 'write',
        ok: true,
        arguments: JSON.stringify({
          path: '/workspace/src/a.ts',
          content: 'const a = 1;\n',
        }),
        detail: '写入 1 行',
      },
    ];

    renderViewer({ tab: 'diff', toolCalls });

    expect(screen.getByText(/补丁由工具调用参数重建/)).toBeTruthy();
    expect(screen.getByText('#4 · write')).toBeTruthy();
    expect(screen.getByText(/const a = 1;/)).toBeTruthy();
  });

  it('改动页没有记录时明说,参数截断时给提示与原文', () => {
    renderViewer({ tab: 'diff', toolCalls: [] });
    expect(screen.getByText('本次会话没有该文件的修改记录。')).toBeTruthy();

    mockFetch({});
    renderViewer({
      tab: 'diff',
      toolCalls: [
        {
          seq: 1,
          name: 'write',
          ok: true,
          arguments: '{"path":"/workspace/src/a.ts","content":"const',
          detail: '',
        },
      ],
    });
    expect(screen.getByText(/参数被截断/)).toBeTruthy();
    expect(screen.getByText(/原始参数/)).toBeTruthy();
  });

  it('有会话 id 时改动页用服务端完整补丁,并显示增删行数与时间', async () => {
    mockFetch(
      toolCallsPage([
        {
          id: 'tc-1',
          tool_name: 'write',
          ok: true,
          paths: ['/workspace/src/a.ts'],
          patch: '@@ -0,0 +1,2 @@\n+const a = 1;\n+const b = 2;',
          lines_added: 2,
          lines_removed: 1,
          patch_truncated: false,
          created_at: '2026-10-01T02:03:00Z',
        },
      ]),
    );

    renderViewer({ tab: 'diff', conversationId: 'conv-1' });

    expect(
      await screen.findByText(/服务端补丁 · 完整 unified diff/),
    ).toBeTruthy();
    expect(screen.getByText('+2')).toBeTruthy();
    expect(screen.getByText('−1')).toBeTruthy();
    expect(screen.getByText(/const a = 1;/)).toBeTruthy();
    expect(screen.queryByText(/参数重建/)).toBeNull();
  });

  it('服务端降级时回退到工具参数本地重建', async () => {
    mockFetch(toolCallsPage([], true));

    renderViewer({
      tab: 'diff',
      conversationId: 'conv-1',
      toolCalls: [
        {
          seq: 4,
          name: 'write',
          ok: true,
          arguments: JSON.stringify({
            path: '/workspace/src/a.ts',
            content: 'const a = 1;\n',
          }),
          detail: '写入 1 行',
        },
      ],
    });

    expect(screen.getByText(/补丁由工具调用参数重建/)).toBeTruthy();
    expect(screen.getByText('#4 · write')).toBeTruthy();
    expect(screen.getByText(/const a = 1;/)).toBeTruthy();
  });

  it('单条补丁超上限时在对应 section 标注截断', async () => {
    mockFetch(
      toolCallsPage([
        {
          id: 'tc-1',
          tool_name: 'edit',
          ok: true,
          paths: ['/workspace/src/a.ts'],
          patch: '@@ -1 +1 @@\n-const a = 1;\n+const a = 2;',
          lines_added: 1,
          lines_removed: 1,
          patch_truncated: true,
          created_at: '2026-10-01T02:03:00Z',
        },
      ]),
    );

    renderViewer({ tab: 'diff', conversationId: 'conv-1' });

    expect(await screen.findByText('补丁超上限已截断')).toBeTruthy();
  });

  it('「跳到上次修改」先切到改动页,再滚到最后一个补丁', async () => {
    const scrollIntoView = vi
      .spyOn(Element.prototype, 'scrollIntoView')
      .mockImplementation(() => undefined);
    const toolCalls: ToolCall[] = [
      {
        seq: 4,
        name: 'write',
        ok: true,
        arguments: JSON.stringify({
          path: '/workspace/src/a.ts',
          content: 'const a = 1;\n',
        }),
        detail: '写入 1 行',
      },
    ];
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    // 受控外壳:模拟 chat-page 的真实行为(切页签由上层状态决定)。
    function Harness() {
      const [tab, setTab] = useState<'content' | 'diff'>('content');
      return (
        <SandboxFileViewer
          sandbox={sandbox}
          sandboxLoading={false}
          path="/workspace/src/a.ts"
          tab={tab}
          onTabChange={setTab}
          toolCalls={toolCalls}
          onClose={() => undefined}
        />
      );
    }

    render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '跳到上次修改' }));

    expect(await screen.findByText('#4 · write')).toBeTruthy();
    expect(scrollIntoView).toHaveBeenCalled();
    scrollIntoView.mockRestore();
  });

  it('内容页签也能凭服务端补丁启用「跳到上次修改」', async () => {
    mockFetch(
      toolCallsPage([
        {
          id: 'tc-1',
          tool_name: 'write',
          ok: true,
          paths: ['/workspace/src/a.ts'],
          patch: '@@ -0,0 +1 @@\n+const a = 1;',
          lines_added: 1,
          lines_removed: 0,
          patch_truncated: false,
          created_at: '2026-10-01T02:03:00Z',
        },
      ]),
    );
    const onTabChange = vi.fn();

    renderViewer({ tab: 'content', conversationId: 'conv-1', onTabChange });

    const button = screen.getByRole('button', { name: '跳到上次修改' });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    expect(onTabChange).toHaveBeenCalledWith('diff');
  });

  it('没有补丁时「跳到上次修改」禁用并说明原因', () => {
    renderViewer({ tab: 'diff', toolCalls: [] });

    const button = screen.getByRole('button', { name: '跳到上次修改' });
    expect(button).toBeDisabled();
    expect(button.getAttribute('title')).toContain('无法跳转');
  });

  it('Esc 关闭浮层', () => {
    const onClose = vi.fn();
    renderViewer({ onClose });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(onClose).toHaveBeenCalled();
  });
});