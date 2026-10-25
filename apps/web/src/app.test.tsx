import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { accessToken } from '@/lib/auth';
import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';

import { App } from './app';

vi.mock('@/lib/auth', () => ({
  authMode: vi.fn(() => 'token'),
  accessToken: vi.fn(async () => null),
  authHeaders: vi.fn(async () => ({})),
  accountLabel: vi.fn(async () => null),
  getStoredToken: vi.fn(() => null),
  setStoredToken: vi.fn(),
  rememberLoginRedirect: vi.fn(),
  takeLoginRedirect: vi.fn(() => '/'),
  callbackUri: vi.fn(() => 'http://localhost:3000/callback'),
  signIn: vi.fn(async () => {}),
  signOut: vi.fn(async () => {}),
  handleSignInCallback: vi.fn(async () => {}),
}));

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    list: vi.fn(async () => []),
    page: vi.fn(async () => ({ items: [], next_cursor: null })),
    create: vi.fn(),
    get: vi.fn(),
    messages: vi.fn(),
    postMessage: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
  streamMessage: vi.fn(),
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      list: vi.fn(async () => []),
      get: vi.fn(),
      getPlan: vi.fn(),
      steps: vi.fn(async () => []),
      approve: vi.fn(),
      reject: vi.fn(),
      confirm: vi.fn(),
      artifact: vi.fn(),
      instructions: vi.fn(),
      retry: vi.fn(),
      resume: vi.fn(),
      generatePlan: vi.fn(),
      updatePlan: vi.fn(),
      queueInstruction: vi.fn(),
      create: vi.fn(),
    },
  };
});

vi.mock('@/lib/workspace-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/workspace-api')>(
    '@/lib/workspace-api',
  );
  return {
    ...actual,
    workspaceApi: {
      workspaces: vi.fn(),
      defaultWorkspace: vi.fn(),
      artifacts: vi.fn(async () => []),
    },
  };
});

vi.mock('@/lib/account-api', () => ({
  accountApi: {
    organization: vi.fn(async () => {
      throw new Error('未配置 account-service');
    }),
  },
}));

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

beforeEach(() => {
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([workspace]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
});

function renderApp(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('App 路由', () => {
  beforeEach(() => {
    // 路由用例关心页面本身;守卫要求已登录
    vi.mocked(accessToken).mockResolvedValue('access-jwt');
  });

  it('/ 渲染工作台', async () => {
    renderApp('/');
    // 工作台是路由级懒加载（R-10），覆盖率插桩 + 并行 worker 下首帧可能超过
    // findBy 默认 1s；放宽到 5s，避免门禁偶发超时（不是功能断言放宽）。
    expect(
      await screen.findByText('开始一个新会话', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
  });

  it('/landing 旧链接跳转到独立官网', async () => {
    renderApp('/landing');
    expect(
      await screen.findByRole('link', { name: '继续前往官网' }),
    ).toHaveAttribute('href', 'http://localhost:3000/');
  });

  it('/products 已下线(渲染 404)', () => {
    renderApp('/products');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('/about 渲染关于页', async () => {
    renderApp('/about');
    expect(
      await screen.findByRole('heading', { name: '关于 beauty moon' }),
    ).toBeInTheDocument();
  });

  it('未知路由渲染 404', () => {
    renderApp('/no-such-page');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('所有控制台路由都包在骨架里（侧边导航 + 主区域）', async () => {
    renderApp('/');
    expect(
      await screen.findByRole('complementary', { name: '控制台导航' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
  });
});

describe('登录门（§10）', () => {
  beforeEach(() => {
    vi.mocked(accessToken).mockResolvedValue(null);
  });

  it('/login 是独立整页（不带控制台导航）', () => {
    renderApp('/login');
    expect(
      screen.queryByRole('complementary', { name: '控制台导航' }),
    ).toBeNull();
    expect(screen.getByRole('heading', { name: '登录' })).toBeInTheDocument();
  });

  it('未登录：受保护页被送回 /login', async () => {
    renderApp('/runs');
    expect(
      await screen.findByRole('heading', { name: '登录' }),
    ).toBeInTheDocument();
  });

  it('已登录：受保护页正常渲染', async () => {
    vi.mocked(accessToken).mockResolvedValue('access-jwt');
    renderApp('/');
    expect(await screen.findByText('开始一个新会话')).toBeInTheDocument();
  });

  it('公开页（/about）不受守卫影响', async () => {
    renderApp('/about');
    expect(
      await screen.findByRole('heading', { name: '关于 beauty moon' }),
    ).toBeInTheDocument();
  });
});
