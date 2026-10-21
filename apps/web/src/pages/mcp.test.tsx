import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MCPServer } from '@/lib/mcp-api';
import { mcpApi } from '@/lib/mcp-api';

import { McpPage } from './mcp';

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
      startOAuth: vi.fn(),
      disconnectOAuth: vi.fn(),
    },
  };
});

const httpServer: MCPServer = {
  id: 'm1',
  name: 'github',
  transport: 'http',
  url: 'https://example.com/mcp',
  enabled: true,
  auto_approve: false,
  has_token: true,
  token_hint: '1234',
  command: null,
  args: [],
  runtime: 'python',
  image: null,
  has_env: false,
  env_keys: [],
  tools_allowlist: [],
  tools: [
    {
      name: 'search_issues',
      description: '按关键词搜索 issue',
      input_schema: {
        type: 'object',
        properties: { query: { type: 'string' }, repo: { type: 'string' } },
      },
    },
  ],
  timeout_seconds: 10,
  last_status: 'ok',
  last_checked_at: '2026-10-20T10:00:00+00:00',
  last_error: null,
  has_oauth: false,
  oauth_authorized: false,
  oauth_scopes: [],
  oauth_expires_at: null,
  oauth_authorization_server: null,
  oauth_error: null,
  created_at: '2026-10-20T00:00:00+00:00',
};

const stdioServer: MCPServer = {
  id: 'm2',
  name: 'filesystem',
  transport: 'stdio',
  url: '',
  enabled: true,
  auto_approve: true,
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
  has_oauth: false,
  oauth_authorized: false,
  oauth_scopes: [],
  oauth_expires_at: null,
  oauth_authorization_server: null,
  oauth_error: null,
  created_at: '2026-10-20T00:00:00+00:00',
};

/** 服务端回 401 的 server:状态是 auth_required,不是普通连接失败。 */
const authServer: MCPServer = {
  ...httpServer,
  id: 'm3',
  name: 'notion',
  url: 'https://mcp.notion.example/mcp',
  last_status: 'auth_required',
  last_checked_at: '2026-10-20T11:00:00+00:00',
  last_error: 'MCPAuthRequired: MCP 服务端要求授权(HTTP 401)',
};

/** 走过浏览器授权(http)但当前没有有效 token 的 server:详情里应提示「去授权」。 */
const oauthServer: MCPServer = {
  ...httpServer,
  id: 'm4',
  name: 'linear',
  url: 'https://mcp.linear.example/mcp',
  has_token: false,
  token_hint: null,
  last_status: null,
  last_checked_at: null,
  last_error: null,
  has_oauth: true,
  oauth_authorized: false,
  oauth_scopes: ['mcp:read'],
  oauth_expires_at: null,
  oauth_authorization_server: 'https://auth.linear.example',
};

function renderPage(initialEntry = '/mcp') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <McpPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openForm() {
  fireEvent.click(screen.getByRole('button', { name: '添加 MCP server' }));
  await screen.findByLabelText(/名称/);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(mcpApi.list).mockResolvedValue([httpServer, stdioServer]);
  vi.mocked(mcpApi.update).mockResolvedValue(httpServer);
  vi.mocked(mcpApi.remove).mockResolvedValue(undefined);
  // 用 hash 地址模拟授权页跳转:jsdom 只实现了 hash 导航,不污染测试输出
  vi.mocked(mcpApi.startOAuth).mockResolvedValue({
    authorize_url: '#oauth',
    expires_in: 600,
  });
  vi.mocked(mcpApi.disconnectOAuth).mockResolvedValue(undefined);
});

describe('McpPage', () => {
  it('列表区分 HTTP 与 stdio:stdio 显示命令,不回显环境变量值', async () => {
    renderPage();

    expect((await screen.findAllByText('github')).length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        'node npx -y @modelcontextprotocol/server-filesystem /tmp',
      ),
    ).toBeTruthy();
    expect(
      screen.getAllByText('https://example.com/mcp').length,
    ).toBeGreaterThan(0);
    // 默认选中第一个:HTTP server 的传输标签在详情里
    expect(screen.getAllByText('HTTP').length).toBeGreaterThan(0);
    // 列表上就带连接失败徽标,不必先选中该 server
    expect(screen.getByText('连接失败')).toBeTruthy();

    // 切到 stdio server:传输方式与命令定义摊开,错误原因可见
    fireEvent.click(screen.getByRole('button', { name: /filesystem/ }));
    expect(await screen.findByText('stdio')).toBeTruthy();
    expect(screen.getByText('连接失败:沙箱未启用')).toBeTruthy();

    // 读取接口不返回 env 值:页面也不应该有回显位(只列 key 名)
    expect(screen.queryByText(/GITHUB_TOKEN=/)).toBeNull();
    expect(screen.queryByText(/ghp_/)).toBeNull();
  });

  it('详情把连接定义摊开:地址、Token 尾号、工具清单', async () => {
    renderPage();
    await screen.findAllByText('github');

    expect(screen.getByText('••••1234')).toBeTruthy();
    expect(
      screen.getAllByText('https://example.com/mcp').length,
    ).toBeGreaterThan(0);
    expect(screen.getByText('search_issues')).toBeTruthy();
    expect(screen.getByText('按关键词搜索 issue')).toBeTruthy();
    expect(screen.getByText('2 个参数')).toBeTruthy();
  });

  it('HTTP 传输提交 url/token,不夹带 stdio 字段', async () => {
    vi.mocked(mcpApi.create).mockResolvedValue(httpServer);
    renderPage();
    await screen.findAllByText('github');
    await openForm();

    fireEvent.change(screen.getByLabelText(/名称/), {
      target: { value: 'demo' },
    });
    fireEvent.change(screen.getByLabelText('Token(可选)'), {
      target: { value: 'secret' },
    });
    // 信任该 server:注册时就打开免确认执行(默认关)
    fireEvent.click(screen.getByRole('switch', { name: '免确认执行(注册)' }));
    fireEvent.click(screen.getByRole('button', { name: '注册 MCP server' }));

    await waitFor(() => {
      expect(mcpApi.create).toHaveBeenCalledWith({
        transport: 'http',
        name: 'demo',
        url: 'http://127.0.0.1:8010/mcp',
        auto_approve: true,
        token: 'secret',
      });
    });
  });

  it('stdio 传输:参数/环境变量按行解析,提交后清空敏感输入', async () => {
    vi.mocked(mcpApi.create).mockResolvedValue(stdioServer);
    renderPage();
    await screen.findAllByText('github');
    await openForm();

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
        auto_approve: false,
      });
    });
    // 成功后表单关闭;重开时敏感输入不能留在内存里
    await waitFor(() => {
      expect(
        screen.queryByLabelText('环境变量(KEY=VALUE,一行一个)'),
      ).toBeNull();
    });
    await openForm();
    fireEvent.click(screen.getByRole('tab', { name: 'stdio(本地进程)' }));
    expect(
      screen.getByLabelText('环境变量(KEY=VALUE,一行一个)'),
    ).toHaveProperty('value', '');
  });

  it('stdio 未填命令或命令含空格时不提交', async () => {
    renderPage();
    await screen.findAllByText('github');
    await openForm();

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
      auth_required: false,
    });
    renderPage();
    await screen.findAllByText('github');

    fireEvent.click(screen.getByRole('button', { name: '测试/刷新' }));

    expect(await screen.findByText('测试失败:沙箱未启用')).toBeTruthy();
  });

  it('401 不算连接失败:列表与详情都提示需要重新授权', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([authServer]);
    renderPage();

    expect((await screen.findAllByText('notion')).length).toBeGreaterThan(0);
    // 列表徽标与详情徽标都是「需要授权」,不是「连接失败」
    expect(screen.getAllByText('需要授权').length).toBeGreaterThan(0);
    expect(
      screen.getByText('需要重新授权:该 server 拒绝了当前凭证(HTTP 401)'),
    ).toBeTruthy();
    expect(screen.getByText(/MCP 服务端要求授权/)).toBeTruthy();
    expect(screen.queryByText('连接失败')).toBeNull();
  });

  it('测试返回 auth_required 时给重新授权指引', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([authServer]);
    vi.mocked(mcpApi.test).mockResolvedValue({
      ok: false,
      detail:
        '服务端要求授权(HTTP 401):请重新授权或更新该 server 的 token 后重试',
      tools: [],
      auth_required: true,
    });
    renderPage();
    await screen.findAllByText('notion');

    fireEvent.click(screen.getByRole('button', { name: '测试/刷新' }));

    expect(
      await screen.findByText(/请重新授权或更新该 server 的 token 后重试/),
    ).toBeTruthy();
  });

  it('详情可切换免确认执行:走 PATCH auto_approve', async () => {
    renderPage();
    await screen.findAllByText('github');

    // httpServer.auto_approve = false → 打开即 PATCH true
    expect(screen.getByText(/每次调用需确认/)).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: '免确认执行' }));
    await waitFor(() => {
      expect(mcpApi.update).toHaveBeenCalledWith('m1', { auto_approve: true });
    });
  });

  it('停用走 PATCH,删除二次确认后调用接口', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    await screen.findAllByText('github');

    fireEvent.click(screen.getByRole('button', { name: '停用' }));
    await waitFor(() => {
      expect(mcpApi.update).toHaveBeenCalledWith('m1', { enabled: false });
    });

    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => {
      expect(mcpApi.remove).toHaveBeenCalledWith('m1');
    });
    expect(confirm).toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('未授权的 http server 显示「去授权」,点击调用 startOAuth', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([oauthServer]);
    renderPage();
    await screen.findAllByText('linear');

    expect(screen.getByText('未授权')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '去授权' }));

    await waitFor(() => {
      expect(mcpApi.startOAuth).toHaveBeenCalledWith('m4');
    });
  });

  it('已授权的 http server 提供「重新授权」与「解除授权」,解除走接口', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([
      {
        ...oauthServer,
        oauth_authorized: true,
        oauth_expires_at: '2026-10-22T00:00:00+00:00',
        last_status: 'ok',
      },
    ]);
    renderPage();
    await screen.findAllByText('linear');

    expect(screen.getByText('已授权')).toBeTruthy();
    expect(screen.getByRole('button', { name: '重新授权' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '解除授权' }));

    await waitFor(() => {
      expect(mcpApi.disconnectOAuth).toHaveBeenCalledWith('m4');
    });
  });

  it('?oauth=success 展示成功提示(含 server 名)', async () => {
    renderPage('/mcp?oauth=success&server=linear');

    expect(await screen.findByText('浏览器授权成功:linear')).toBeTruthy();
  });

  it('?oauth=error 展示错误提示', async () => {
    renderPage('/mcp?oauth=error&error=access_denied');

    expect(
      await screen.findByText('浏览器授权失败:access_denied'),
    ).toBeTruthy();
  });
});
