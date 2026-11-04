import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  ConnectorConnection,
  ConnectorProvider,
} from '@/lib/connector-api';
import { connectorApi } from '@/lib/connector-api';
import { useNotificationsStore } from '@/stores/notifications-store';

import { ConnectorsPage } from './connectors';

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
    },
  };
});

const demoProvider: ConnectorProvider = {
  provider: 'demo',
  display_name: '示例连接器',
  description: '消息与文档能力演示。',
  auth_kind: 'oauth_user',
  scopes: ['demo:read', 'demo:write'],
  configured: true,
  tools: [
    { name: 'Demo__send_message', description: '发送消息', risk: 'write' },
    { name: 'Demo__search_docs', description: '搜索文档', risk: 'read' },
  ],
};

const notionProvider: ConnectorProvider = {
  provider: 'notion',
  display_name: 'Notion',
  description: '页面与数据库。',
  auth_kind: 'oauth_user',
  scopes: ['read_content'],
  configured: true,
  tools: [
    { name: 'Notion__query_database', description: '查询数据库', risk: 'read' },
  ],
};

const activeConnection: ConnectorConnection = {
  id: 'c1',
  provider: 'demo',
  status: 'active',
  account: 'me@example.com',
  auth_kind: 'oauth_user',
  provider_account_id: 'user-1',
  external_workspace: 'workspace-1',
  enabled: true,
  scopes: ['demo:read'],
  expires_at: '2026-12-01T08:00:00+00:00',
  refresh_expires_at: '2027-02-01T08:00:00+00:00',
  last_refreshed_at: '2026-11-01T08:00:00+00:00',
  last_checked_at: '2026-11-02T08:00:00+00:00',
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-11-02T08:00:00+00:00',
};

const expiredConnection: ConnectorConnection = {
  id: 'c2',
  provider: 'notion',
  status: 'expired',
  account: 'team@example.com',
  enabled: true,
  last_error_code: 'connector.auth_expired',
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-11-02T08:00:00+00:00',
};

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
  vi.mocked(connectorApi.listProviders).mockResolvedValue([
    demoProvider,
    notionProvider,
  ]);
  vi.mocked(connectorApi.listConnections).mockResolvedValue([activeConnection]);
  vi.mocked(connectorApi.getConnection).mockResolvedValue(activeConnection);
  // 用 hash 地址模拟第三方授权页跳转:jsdom 只实现了 hash 导航
  vi.mocked(connectorApi.authorize).mockResolvedValue({
    provider: 'notion',
    authorize_url: '#oauth',
    expires_at: '2026-11-03T10:05:00+00:00',
  });
  vi.mocked(connectorApi.updateConnection).mockResolvedValue({
    ...activeConnection,
    enabled: false,
  });
  vi.mocked(connectorApi.removeConnection).mockResolvedValue({
    ...activeConnection,
    status: 'revoked',
    enabled: false,
    revoked_at: '2026-11-03T10:00:00+00:00',
  });
  vi.mocked(connectorApi.refreshConnection).mockResolvedValue({
    ...activeConnection,
    expires_at: '2027-01-01T00:00:00+00:00',
  });
  vi.mocked(connectorApi.checkConnection).mockResolvedValue({
    ...activeConnection,
  });
});

describe('ConnectorsPage', () => {
  it('加载中提示,加载完成后按 provider 渲染卡片', async () => {
    let resolveProviders: (value: ConnectorProvider[]) => void = () => {};
    vi.mocked(connectorApi.listProviders).mockReturnValue(
      new Promise((resolve) => {
        resolveProviders = resolve;
      }),
    );
    renderPage();

    expect(screen.getByText('正在加载连接器…')).toBeTruthy();

    await act(async () => {
      resolveProviders([demoProvider, notionProvider]);
    });

    expect(await screen.findByText('示例连接器')).toBeTruthy();
    expect(screen.getByText('Notion')).toBeTruthy();
    expect(screen.getByText('me@example.com')).toBeTruthy();
    expect(screen.getByText('已连接')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '管理 示例连接器' }),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '连接 Notion' })).toBeTruthy();
  });

  it('目录为空时给空状态', async () => {
    vi.mocked(connectorApi.listProviders).mockResolvedValue([]);
    vi.mocked(connectorApi.listConnections).mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('暂无可用连接器')).toBeTruthy();
  });

  it('目录加载失败展示错误与重试', async () => {
    vi.mocked(connectorApi.listProviders)
      .mockRejectedValueOnce(new Error('连接器服务不可用'))
      .mockResolvedValueOnce([demoProvider]);
    renderPage();

    expect(await screen.findByText('连接器目录加载失败')).toBeTruthy();
    expect(screen.getByText('连接器服务不可用')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('示例连接器')).toBeTruthy();
  });

  it('失效连接显示「需要重新连接」,点击重新发起授权', async () => {
    vi.mocked(connectorApi.listConnections).mockResolvedValue([
      activeConnection,
      expiredConnection,
    ]);
    renderPage();

    expect(await screen.findByText('需要重新连接')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '重新连接 Notion' }));

    await waitFor(() => {
      expect(connectorApi.authorize).toHaveBeenCalledWith('notion', {
        redirect_uri: expect.stringContaining('/connectors'),
      });
    });
  });

  it('点击「连接」发起授权并整页跳转', async () => {
    renderPage();
    await screen.findByText('Notion');

    fireEvent.click(screen.getByRole('button', { name: '连接 Notion' }));

    await waitFor(() => {
      expect(connectorApi.authorize).toHaveBeenCalledWith('notion', {
        redirect_uri: expect.stringContaining('/connectors'),
      });
    });
    await waitFor(() => {
      expect(window.location.hash).toBe('#oauth');
    });
  });

  it('回调成功 toast 并清掉 query', async () => {
    renderPage('/connectors?provider=demo&status=connected&connection_id=c1');

    await screen.findByText('示例连接器');
    await waitFor(() => {
      expect(toasts().some((toast) => toast.title === '连接器已连接')).toBe(
        true,
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/connectors');
    });
  });

  it('回调失败 toast 失败原因并清掉 query', async () => {
    renderPage('/connectors?provider=notion&status=error&code=access_denied');

    await screen.findByText('Notion');
    await waitFor(() => {
      const toast = toasts().find((item) => item.title === '连接器连接失败');
      expect(toast?.description).toBe('你在授权页取消了授权。');
    });
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/connectors');
    });
  });

  it('兼容旧回调形状:?connected=<id>', async () => {
    renderPage('/connectors?connected=c1');

    await screen.findByText('示例连接器');
    await waitFor(() => {
      const toast = toasts().find((item) => item.title === '连接器已连接');
      expect(toast?.description).toContain('me@example.com');
    });
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/connectors');
    });
  });

  it('兼容旧回调失败形状:?error=<code>', async () => {
    renderPage('/connectors?error=connector.scope_missing');

    await screen.findByText('示例连接器');
    await waitFor(() => {
      const toast = toasts().find((item) => item.title === '连接器连接失败');
      expect(toast?.description).toBe(
        '授权缺少必要权限,请重新连接并同意全部 scope。',
      );
    });
  });

  it('详情抽屉展示账号/scope/工具,并支持检查连通性', async () => {
    vi.mocked(connectorApi.checkConnection).mockResolvedValue({
      ...activeConnection,
      status: 'expired',
      last_error_code: 'connector.auth_expired',
    });
    renderPage();
    await screen.findByText('示例连接器');

    fireEvent.click(screen.getByRole('button', { name: '管理 示例连接器' }));

    const dialog = await screen.findByRole('dialog', { name: /连接详情/ });
    // 账号既是抽屉标题(无自定义名时)也是「账号」行,允许重复
    expect(
      within(dialog).getAllByText('me@example.com').length,
    ).toBeGreaterThan(0);
    expect(within(dialog).getByText('demo:read')).toBeTruthy();
    expect(within(dialog).getByText(/demo:write/)).toBeTruthy();
    expect(within(dialog).getByText('Demo__send_message')).toBeTruthy();
    expect(within(dialog).getByText('搜索文档')).toBeTruthy();
    expect(within(dialog).getByText('workspace-1')).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: '检查连通性' }));

    expect(await within(dialog).findByText('连通性检查失败')).toBeTruthy();
    expect(
      within(dialog).getByText(/授权已失效或缺少权限,请点「重新连接」/),
    ).toBeTruthy();
  });

  it('检查连通性成功时提示正常', async () => {
    renderPage();
    await screen.findByText('示例连接器');
    fireEvent.click(screen.getByRole('button', { name: '管理 示例连接器' }));

    const dialog = await screen.findByRole('dialog', { name: /连接详情/ });
    fireEvent.click(within(dialog).getByRole('button', { name: '检查连通性' }));

    expect(await within(dialog).findByText('连通性正常')).toBeTruthy();
  });

  it('详情里可停用连接,走 PATCH enabled=false', async () => {
    renderPage();
    await screen.findByText('示例连接器');
    fireEvent.click(screen.getByRole('button', { name: '管理 示例连接器' }));

    const dialog = await screen.findByRole('dialog', { name: /连接详情/ });
    fireEvent.click(within(dialog).getByRole('switch', { name: '启用连接' }));

    await waitFor(() => {
      expect(connectorApi.updateConnection).toHaveBeenCalledWith('c1', {
        enabled: false,
      });
    });
  });

  it('刷新凭证走 refresh 接口', async () => {
    renderPage();
    await screen.findByText('示例连接器');
    fireEvent.click(screen.getByRole('button', { name: '管理 示例连接器' }));

    const dialog = await screen.findByRole('dialog', { name: /连接详情/ });
    fireEvent.click(within(dialog).getByRole('button', { name: '刷新凭证' }));

    await waitFor(() => {
      expect(connectorApi.refreshConnection).toHaveBeenCalledWith('c1');
    });
  });

  it('解除连接需确认,确认后调用删除接口', async () => {
    renderPage();
    await screen.findByText('示例连接器');
    fireEvent.click(screen.getByRole('button', { name: '管理 示例连接器' }));

    const drawer = await screen.findByRole('dialog', { name: /连接详情/ });
    fireEvent.click(within(drawer).getByRole('button', { name: '解除连接' }));

    expect(await screen.findByText('解除连接？')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认解除' }));

    await waitFor(() => {
      expect(connectorApi.removeConnection).toHaveBeenCalledWith('c1');
    });
  });
});
