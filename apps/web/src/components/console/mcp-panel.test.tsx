import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MCPServer } from '@/lib/mcp-api';
import { mcpApi } from '@/lib/mcp-api';

import { McpPanel } from './mcp-panel';

vi.mock('@/lib/mcp-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/mcp-api')>('@/lib/mcp-api');
  return {
    ...actual,
    mcpApi: {
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      test: vi.fn(),
    },
  };
});

const httpServer: MCPServer = {
  id: 'm1',
  name: 'github',
  transport: 'http',
  url: 'https://example.com/mcp',
  enabled: true,
  has_token: true,
  token_hint: '1234',
  command: null,
  args: [],
  runtime: 'python',
  image: null,
  has_env: false,
  env_keys: [],
  tools_allowlist: [],
  tools: [],
  timeout_seconds: 10,
  last_status: 'ok',
  last_checked_at: null,
  last_error: null,
  created_at: '2026-10-20T00:00:00+00:00',
};

const stdioServer: MCPServer = {
  id: 'm2',
  name: 'filesystem',
  transport: 'stdio',
  url: '',
  enabled: true,
  has_token: false,
  token_hint: null,
  command: 'npx',
  args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
  runtime: 'node',
  image: 'node:22-slim',
  has_env: true,
  env_keys: ['GITHUB_TOKEN', 'LOG_LEVEL'],
  tools_allowlist: [],
  tools: [],
  timeout_seconds: 10,
  last_status: 'error',
  last_checked_at: null,
  last_error: '沙箱未启用',
  created_at: '2026-10-20T00:00:00+00:00',
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <McpPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(mcpApi.list).mockResolvedValue([httpServer, stdioServer]);
});

describe('McpPanel', () => {
  it('列表区分 HTTP 与 stdio:stdio 显示命令/运行时/环境变量个数,不显示 URL', async () => {
    renderPanel();

    expect(await screen.findByText('github')).toBeTruthy();
    expect(screen.getByText('https://example.com/mcp')).toBeTruthy();
    expect(
      screen.getByText(
        'node npx -y @modelcontextprotocol/server-filesystem /tmp',
      ),
    ).toBeTruthy();
    expect(screen.getByText('运行时 Node')).toBeTruthy();
    expect(screen.getByText('镜像 node:22-slim')).toBeTruthy();
    expect(screen.getByText('已配置 2 个环境变量')).toBeTruthy();
    // 读取接口不返回 env 值:面板也不应该有回显位
    expect(screen.queryByText(/GITHUB_TOKEN=/)).toBeNull();
    expect(screen.getByText('连接失败:沙箱未启用')).toBeTruthy();
    expect(screen.getByText('HTTP')).toBeTruthy();
    expect(screen.getByText('stdio')).toBeTruthy();
  });

  it('HTTP 传输提交 url/token,不夹带 stdio 字段', async () => {
    vi.mocked(mcpApi.create).mockResolvedValue(httpServer);
    renderPanel();
    await screen.findByText('github');

    fireEvent.change(screen.getByLabelText(/名称/), {
      target: { value: 'demo' },
    });
    fireEvent.change(screen.getByLabelText('Token(可选)'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '注册 MCP server' }));

    await waitFor(() => {
      expect(mcpApi.create).toHaveBeenCalledWith({
        transport: 'http',
        name: 'demo',
        url: 'http://127.0.0.1:8010/mcp',
        token: 'secret',
      });
    });
  });

  it('stdio 传输:参数/环境变量按行解析,提交后清空敏感输入', async () => {
    vi.mocked(mcpApi.create).mockResolvedValue(stdioServer);
    renderPanel();
    await screen.findByText('github');

    fireEvent.click(screen.getByRole('tab', { name: 'stdio(本地进程)' }));
    fireEvent.change(screen.getByLabelText(/名称/), {
      target: { value: 'filesystem' },
    });
    fireEvent.change(screen.getByLabelText(/命令/), {
      target: { value: ' npx ' },
    });
    fireEvent.change(screen.getByLabelText('参数(一行一个)'), {
      target: {
        value: '-y\n@modelcontextprotocol/server-filesystem\n\n/tmp',
      },
    });
    fireEvent.change(screen.getByLabelText('环境变量(KEY=VALUE,一行一个)'), {
      target: { value: '# token\nGITHUB_TOKEN=ghp_x\nLOG_LEVEL=debug' },
    });
    fireEvent.change(screen.getByLabelText('运行时'), {
      target: { value: 'node' },
    });
    fireEvent.change(screen.getByLabelText('镜像(可选)'), {
      target: { value: 'node:22-slim' },
    });
    fireEvent.click(screen.getByRole('button', { name: '注册 MCP server' }));

    await waitFor(() => {
      expect(mcpApi.create).toHaveBeenCalledWith({
        transport: 'stdio',
        name: 'filesystem',
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
        env: { GITHUB_TOKEN: 'ghp_x', LOG_LEVEL: 'debug' },
        runtime: 'node',
        image: 'node:22-slim',
      });
    });
    await waitFor(() => {
      expect(
        screen.getByLabelText('环境变量(KEY=VALUE,一行一个)'),
      ).toHaveProperty('value', '');
    });
  });

  it('stdio 未填命令或命令含空格时不提交', async () => {
    renderPanel();
    await screen.findByText('github');

    fireEvent.click(screen.getByRole('tab', { name: 'stdio(本地进程)' }));
    fireEvent.change(screen.getByLabelText(/名称/), {
      target: { value: 'filesystem' },
    });

    const submit = screen.getByRole('button', { name: '注册 MCP server' });
    expect(submit).toBeDisabled();
    expect(screen.queryByLabelText(/Server URL/)).toBeNull();

    // 命令只接受可执行名:带空格的命令(参数应放参数框)会被拒绝并提示
    fireEvent.change(screen.getByLabelText(/命令/), {
      target: { value: 'npx -y' },
    });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText('只允许字母数字与 . _ / -,参数请填到下方参数框'),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/命令/), {
      target: { value: 'npx' },
    });
    expect(submit).toBeEnabled();
  });

  it('测试失败时展示后端 detail', async () => {
    vi.mocked(mcpApi.test).mockResolvedValue({
      ok: false,
      detail: '沙箱未启用',
      tools: [],
    });
    renderPanel();
    await screen.findByText('github');

    fireEvent.click(
      screen.getAllByRole('button', { name: '测试/刷新' })[0] as HTMLElement,
    );

    expect(await screen.findByText('测试失败:沙箱未启用')).toBeTruthy();
  });
});
