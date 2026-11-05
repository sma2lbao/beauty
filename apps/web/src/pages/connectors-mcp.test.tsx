/**
 * /connectors 预设区的页面级流程:连接 = 幂等建 server → 发起授权 → 整页跳转。
 *
 * 这里钉住三件容易回归的事:
 * 1. 调用顺序 ensure 先于 oauth start(否则授权的是别人的 server);
 * 2. 已授权时直接给「去会话使用」/「管理」,不重复 ensure;
 * 3. 授权回跳(?oauth=success|error)只消费一次并清掉 query。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { connectorApi } from '@/lib/connector-api';
import { ApiError } from '@/lib/http';
import { mcpApi, type MCPServer } from '@/lib/mcp-api';
import { useNotificationsStore } from '@/stores/notifications-store';
import type { McpPreset } from '@/features/connectors/mcp-presets';
import { mcpPresetsApi } from '@/features/connectors/mcp-presets';

import { ConnectorsPage } from './connectors';

// 旧目录区也会取数:一并打桩,并固定为空 → 页面只剩 MCP 预设区
vi.mock('@/lib/connector-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/connector-api')>(
    '@/lib/connector-api',
  );
  return {
    ...actual,
    connectorApi: {
      listProviders: vi.fn(),
      authorize: vi.fn(),
      listConnections: vi.fn(),
      getConnection: vi.fn(),
      updateConnection: vi.fn(),
      removeConnection: vi.fn(),
      refreshConnection: vi.fn(),
      checkConnection: vi.fn(),
      connectWithToken: vi.fn(),
    },
  };
});

vi.mock('@/lib/mcp-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/mcp-api')>('@/lib/mcp-api');
  return {
    ...actual,
    mcpApi: { list: vi.fn(), update: vi.fn(), startOAuth: vi.fn() },
  };
});

vi.mock('@/features/connectors/mcp-presets', async () => {
  const actual = await vi.importActual<
    typeof import('@/features/connectors/mcp-presets')
  >('@/features/connectors/mcp-presets');
  return { ...actual, mcpPresetsApi: { list: vi.fn(), ensure: vi.fn() } };
});

const notionPreset: McpPreset = {
  key: 'notion',
  name: 'Notion',
  url: 'https://mcp.notion.com/mcp',
  description: '搜索与读取页面/数据库、更新内容、查看日历。',
  docs_url: null,
};

function makeServer(overrides: Partial<MCPServer> = {}): MCPServer {
  return {
    id: 'srv-1',
    name: 'Notion',
    transport: 'http',
    url: 'https://mcp.notion.com/mcp',
    enabled: true,
    auto_approve: false,
    has_token: false,
    token_hint: null,
    command: null,
    args: [],
    runtime: 'python',
    image: null,
    has_env: false,
    env_keys: [],
    tools_allowlist: [],
    tools: [],
    timeout_seconds: 30,
    last_status: 'ok',
    last_checked_at: null,
    last_error: null,
    has_oauth: true,
    oauth_authorized: false,
    oauth_scopes: [],
    oauth_expires_at: null,
    oauth_authorization_server: null,
    oauth_error: null,
    created_at: '2026-11-04T08:00:00+00:00',
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{location.pathname + location.search}</div>
  );
}

function renderPage(initialEntry = '/connectors') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <ConnectorsPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function toasts() {
  return useNotificationsStore.getState().toasts;
}

beforeEach(() => {
  vi.clearAllMocks();
  useNotificationsStore.getState().reset();
  // jsdom 只实现了 hash 导航:授权跳转用 hash 地址断言,每例前清掉
  window.history.replaceState({}, '', '/');
  vi.mocked(connectorApi.listProviders).mockResolvedValue([]);
  vi.mocked(connectorApi.listConnections).mockResolvedValue([]);
  vi.mocked(mcpPresetsApi.list).mockResolvedValue([notionPreset]);
  vi.mocked(mcpApi.list).mockResolvedValue([]);
  // 用 hash 地址模拟第三方授权页:整页跳转在 jsdom 里只支持 hash
  vi.mocked(mcpApi.startOAuth).mockResolvedValue({
    authorize_url: '#mcp-authorize',
    expires_in: 600,
  });
  vi.mocked(mcpPresetsApi.ensure).mockResolvedValue(makeServer());
  vi.mocked(mcpApi.update).mockResolvedValue(makeServer());
});

describe('ConnectorsPage · MCP 预设', () => {
  it('未连接时点「连接」:先幂等建 server,再发起授权并整页跳转', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '连接 Notion' }));

    await waitFor(() => {
      expect(mcpApi.startOAuth).toHaveBeenCalledWith('srv-1', '/connectors');
    });
    expect(mcpPresetsApi.ensure).toHaveBeenCalledWith('notion');
    // 顺序:ensure 拿到 id 之后才能 startOAuth
    const ensureOrder = vi.mocked(mcpPresetsApi.ensure).mock
      .invocationCallOrder[0];
    const oauthOrder = vi.mocked(mcpApi.startOAuth).mock.invocationCallOrder[0];
    expect(ensureOrder).toBeLessThan(oauthOrder);

    await waitFor(() => {
      expect(window.location.hash).toBe('#mcp-authorize');
    });
  });

  it('已授权时渲染「已连接」,并给「去会话使用」与「管理」链接', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([
      makeServer({
        oauth_authorized: true,
        tools: [
          { name: 'notion_search', description: '搜索', input_schema: {} },
          { name: 'notion_read', description: '读取', input_schema: {} },
        ],
      }),
    ]);
    renderPage();

    expect(await screen.findByText('已连接 · 2 个工具')).toBeTruthy();
    // 「已连接」既在预设卡片、也在下方「我的 MCP 服务」列表里
    expect(screen.getAllByText('已连接').length).toBeGreaterThan(0);
    const open = screen.getByRole('link', { name: '去会话使用 Notion' });
    // 会话页用 ?mcp=<server_id> 预选 MCP server
    expect(open.getAttribute('href')).toBe('/?mcp=srv-1');
    expect(
      screen.getByRole('link', { name: '管理 Notion' }).getAttribute('href'),
    ).toBe('/connectors#mcp-servers');
    // 已连接不需要再建 server / 再授权
    expect(mcpPresetsApi.ensure).not.toHaveBeenCalled();
    expect(mcpApi.startOAuth).not.toHaveBeenCalled();
  });

  it('授权失败后可重新授权:只发起授权,不重复建 server', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([
      makeServer({
        oauth_authorized: false,
        oauth_error: 'invalid_client',
        last_status: 'auth_required',
      }),
    ]);
    renderPage();

    expect(
      await screen.findByText(/上次授权没有成功：invalid_client/),
    ).toBeTruthy();
    expect(screen.getByText('授权失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新授权 Notion' }));

    await waitFor(() => {
      expect(mcpApi.startOAuth).toHaveBeenCalledWith('srv-1', '/connectors');
    });
    expect(mcpPresetsApi.ensure).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(window.location.hash).toBe('#mcp-authorize');
    });
  });

  it('发起授权失败时在卡片上提示,不跳转,可再点重试', async () => {
    vi.mocked(mcpPresetsApi.ensure)
      .mockRejectedValueOnce(new Error('预设创建失败'))
      .mockResolvedValueOnce(makeServer());
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '连接 Notion' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('预设创建失败');
    expect(mcpApi.startOAuth).not.toHaveBeenCalled();
    expect(window.location.hash).not.toBe('#mcp-authorize');

    fireEvent.click(screen.getByRole('button', { name: '连接 Notion' }));
    await waitFor(() => {
      expect(mcpApi.startOAuth).toHaveBeenCalledWith('srv-1', '/connectors');
    });
    expect(mcpPresetsApi.ensure).toHaveBeenCalledTimes(2);
  });

  it('授权失败按错误码分类:可改用令牌,PATCH token 并提示已保存', async () => {
    vi.mocked(mcpApi.startOAuth).mockRejectedValue(
      new ApiError(
        502,
        'mcp.oauth_discovery_failed',
        '该 server 未公布 RFC 9728 OAuth 资源元数据',
      ),
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '连接 Notion' }));

    // 卡片上给的是分类标题与下一步,不是原始 message
    expect(await screen.findByText('该 server 未启用 OAuth 授权')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '改用令牌' }));

    fireEvent.change(await screen.findByLabelText('访问令牌'), {
      target: { value: 'static-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存令牌' }));

    await waitFor(() => {
      expect(mcpApi.update).toHaveBeenCalledWith('srv-1', {
        token: 'static-secret',
      });
    });
    await waitFor(() => {
      expect(toasts().some((toast) => toast.title === '已保存静态令牌')).toBe(
        true,
      );
    });
    // 保存令牌不会发起浏览器授权
    expect(window.location.hash).not.toBe('#mcp-authorize');
  });

  it('授权回跳成功:提示一次并清掉 query', async () => {
    renderPage('/connectors?oauth=success&server=Notion');

    expect(await screen.findByText('MCP 连接器已连接')).toBeTruthy();
    expect(
      screen.getByText('Notion 授权完成，可以在会话里直接使用它的工具了。'),
    ).toBeTruthy();
    await waitFor(() => {
      expect(toasts().some((toast) => toast.title === 'MCP 连接器已连接')).toBe(
        true,
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/connectors');
    });
  });

  it('授权回跳失败:展示后端原因并清掉 query', async () => {
    renderPage('/connectors?oauth=error&server=Notion&error=access_denied');

    expect(await screen.findByText(/access_denied/)).toBeTruthy();
    await waitFor(() => {
      const toast = toasts().find(
        (item) => item.title === 'MCP 连接器授权失败',
      );
      expect(toast?.description).toBe('access_denied');
    });
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/connectors');
    });
  });

  it('已停用的 server 提示到「我的 MCP 服务」启用,不给连接按钮', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([
      makeServer({ enabled: false, oauth_authorized: true }),
    ]);
    renderPage();

    expect((await screen.findAllByText('已停用')).length).toBeGreaterThan(0);
    expect(screen.getByText(/已在「我的 MCP 服务」停用/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '连接 Notion' })).toBeNull();
    expect(
      screen.queryByRole('button', { name: '重新授权 Notion' }),
    ).toBeNull();
    expect(
      screen.getByRole('link', { name: '管理 Notion' }).getAttribute('href'),
    ).toBe('/connectors#mcp-servers');
  });

  it('页内「我的 MCP 服务」区列出已注册的自定义 server', async () => {
    vi.mocked(mcpApi.list).mockResolvedValue([
      makeServer({
        id: 'srv-custom',
        name: 'GitHub',
        url: 'https://mcp.github.example/mcp',
        oauth_authorized: true,
        tools: [
          {
            name: 'search_issues',
            description: '搜索 issue',
            input_schema: {},
          },
        ],
      }),
    ]);
    renderPage();

    expect(await screen.findByText('我的 MCP 服务')).toBeTruthy();
    // 自定义 server(非预设)出现在管理区列表里(列表项 + 选中详情卡会各出现一次)
    expect((await screen.findAllByText('GitHub')).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText('https://mcp.github.example/mcp').length,
    ).toBeGreaterThan(0);
    // 预设区仍按自己的匹配逻辑显示「连接」,不被自定义 server 影响
    expect(screen.getByRole('button', { name: '连接 Notion' })).toBeTruthy();
    expect(mcpApi.startOAuth).not.toHaveBeenCalled();
  });
});
