/**
 * 文件查看器契约:内容页读沙箱文件、改动页从工具参数重建补丁、
 * 没有沙箱/二进制文件都给出实话实说的提示。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
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
    onClose: () => void;
    onTabChange: (tab: 'content' | 'diff') => void;
  }> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SandboxFileViewer
        sandbox={overrides.sandbox === undefined ? sandbox : overrides.sandbox}
        sandboxLoading={overrides.sandboxLoading ?? false}
        path={overrides.path ?? '/workspace/src/a.ts'}
        tab={overrides.tab ?? 'content'}
        onTabChange={overrides.onTabChange ?? (() => undefined)}
        toolCalls={overrides.toolCalls ?? []}
        onClose={overrides.onClose ?? (() => undefined)}
      />
    </QueryClientProvider>,
  );
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

  it('Esc 关闭浮层', () => {
    const onClose = vi.fn();
    renderViewer({ onClose });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(onClose).toHaveBeenCalled();
  });
});
