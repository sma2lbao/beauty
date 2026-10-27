/**
 * 执行面板渲染契约(会话沙箱版)。
 * - 三个 tab(执行步骤 / 文件 / 终端)存在且可切换;
 * - 执行步骤列出工具调用、状态与时间线;
 * - 没有沙箱时「文件 / 终端」诚实给出空态,不渲染假数据;
 * - 有沙箱时文件树按契约请求,点击文件回调给页面打开查看器;
 * - 终端把命令发给沙箱并显示 stdout / 退出码。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SandboxInfo } from '@/lib/sandbox-api';
import type { Run, RunStatus, RunStep } from '@/lib/runs-api';
import type { ToolCall } from '@/lib/presentation';

import { ExecutionPanel } from './execution-panel';

const run: Run = {
  id: 'run-1',
  title: '修一个 bug',
  goal: '修一个 bug',
  status: 'running',
  rounds: 1,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

// ToolCall.arguments 是 JSON 文本字符串,detail 为展示明细。
const toolCalls: ToolCall[] = [
  { seq: 1, name: 'list_files', ok: true, arguments: '{}', detail: '列目录' },
  {
    seq: 2,
    name: 'read_file',
    ok: false,
    arguments: '{"path":"src/app.tsx"}',
    detail: '读文件',
  },
];

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

type PanelOptions = {
  status?: RunStatus;
  steps?: RunStep[];
  sandbox?: SandboxInfo | null;
  sandboxLoading?: boolean;
  sandboxError?: Error | null;
  onOpenFile?: (path: string) => void;
};

function renderPanel(options: PanelOptions = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ExecutionPanel
        run={run}
        status={options.status ?? 'running'}
        toolCalls={toolCalls}
        steps={options.steps ?? []}
        sandbox={options.sandbox ?? null}
        sandboxLoading={options.sandboxLoading}
        sandboxError={options.sandboxError}
        onOpenFile={options.onOpenFile}
      />
    </QueryClientProvider>,
  );
}

function mockFetch(body: unknown, status = 200) {
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
  window.localStorage.clear();
});

describe('ExecutionPanel', () => {
  it('渲染三个 tab,执行步骤列出工具调用与状态', () => {
    renderPanel();
    expect(screen.getByRole('tab', { name: '执行步骤' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '文件' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '终端' })).toBeTruthy();

    expect(screen.getByText('list_files')).toBeTruthy();
    expect(screen.getByText('read_file')).toBeTruthy();
    // 1 成功 1 失败的状态文案
    expect(screen.getByText(/完成/)).toBeTruthy();
    expect(screen.getByText(/失败/)).toBeTruthy();
  });

  it('执行步骤时间线列出节点步骤、尝试次数与耗时', () => {
    renderPanel({
      steps: [
        {
          seq: 3,
          step_id: 'run-1:1:execute:1',
          node: 'execute',
          phase: 'execute',
          attempt: 2,
          round: 1,
          status: 'completed',
          started_at: '2026-10-01T00:00:00Z',
          finished_at: '2026-10-01T00:00:01.500Z',
          duration_ms: 1500,
        },
      ],
    });
    expect(screen.getByTestId('run-step-timeline')).toBeTruthy();
    expect(screen.getByText('执行')).toBeTruthy();
    expect(screen.getByText(/第 2 次尝试/)).toBeTruthy();
    expect(screen.getByText(/1\.5s/)).toBeTruthy();
  });

  it('没有沙箱时文件页给出空态而不是假文件树', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('tab', { name: '文件' }));
    expect(screen.getByText('这个会话还没有运行中的沙箱。')).toBeTruthy();
    expect(screen.getByText(/Agent 在沙箱里跑过工具后/)).toBeTruthy();
  });

  it('沙箱加载中显示提示', () => {
    renderPanel({ sandboxLoading: true });
    fireEvent.click(screen.getByRole('tab', { name: '文件' }));
    expect(screen.getByText('正在查找会话沙箱…')).toBeTruthy();
  });

  it('沙箱文件树按会话沙箱请求,点击文件回调打开查看器', async () => {
    const fetchMock = mockFetch({
      path: '/workspace',
      entries: [
        {
          path: '/workspace/src',
          name: 'src',
          kind: 'dir',
          size_bytes: 0,
          modified: null,
        },
        {
          path: '/workspace/src/app.tsx',
          name: 'app.tsx',
          kind: 'file',
          size_bytes: 42,
          modified: null,
        },
      ],
      truncated: false,
    });
    const onOpenFile = vi.fn();
    renderPanel({ sandbox, onOpenFile });

    fireEvent.click(screen.getByRole('tab', { name: '文件' }));
    expect(await screen.findByText('工作区文件')).toBeTruthy();
    expect(await screen.findByText('src')).toBeTruthy();
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[0]).toBe(
      '/workspace-api/v1/sandboxes/sb-1/tree?path=%2Fworkspace&depth=3',
    );

    fireEvent.click(screen.getByText('src'));
    fireEvent.click(await screen.findByText('app.tsx'));
    expect(onOpenFile).toHaveBeenCalledWith('/workspace/src/app.tsx');
  });

  it('没有沙箱时终端页只给提示', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('tab', { name: '终端' }));
    expect(screen.getByText('这个会话还没有运行中的沙箱。')).toBeTruthy();
  });

  it('终端在沙箱里执行命令并显示输出与退出码', async () => {
    const fetchMock = mockFetch({
      exit_code: 0,
      stdout: 'hi',
      stderr: '',
      duration_ms: 12,
      truncated: false,
    });
    renderPanel({ sandbox });

    fireEvent.click(screen.getByRole('tab', { name: '终端' }));
    expect(await screen.findByText('这是沙箱内的验证终端')).toBeTruthy();

    const input = screen.getByLabelText('终端命令');
    fireEvent.change(input, { target: { value: 'echo hi' } });
    const form = input.closest('form');
    if (!form) throw new Error('终端输入框不在 form 里');
    fireEvent.submit(form);

    expect(await screen.findByText('hi')).toBeTruthy();
    expect(screen.getByText('退出码 0')).toBeTruthy();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/workspace-api/v1/sandboxes/sb-1/exec');
    expect(JSON.parse(String(init.body))).toMatchObject({
      argv: ['sh', '-lc', 'echo hi'],
      cwd: '/workspace',
    });
    await waitFor(() => expect(screen.getByText('echo hi')).toBeTruthy());
  });

  it('沙箱查询失败时给出错误与重试', () => {
    const onRetrySandbox = vi.fn();
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ExecutionPanel
          run={run}
          status="running"
          toolCalls={toolCalls}
          sandbox={null}
          sandboxError={new Error('workspace-service 不可用')}
          onRetrySandbox={onRetrySandbox}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('tab', { name: '文件' }));
    expect(screen.getByText('workspace-service 不可用')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetrySandbox).toHaveBeenCalled();
  });
});
