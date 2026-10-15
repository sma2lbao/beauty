import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { accessToken } from '@/lib/auth';

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

  it('/ 渲染对话页', async () => {
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      '对话',
    );
  });

  it('/products 已下线(渲染 404)', () => {
    renderApp('/products');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('/about 渲染关于页', () => {
    renderApp('/about');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '关于 beauty moon',
    );
  });

  it('未知路由渲染 404', () => {
    renderApp('/no-such-page');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('所有路由都包在站点布局里（页头 + 页脚）', async () => {
    renderApp('/');
    expect(await screen.findByRole('banner')).toBeInTheDocument();
    expect(await screen.findByRole('contentinfo')).toBeInTheDocument();
  });
});

describe('登录门（§10）', () => {
  beforeEach(() => {
    vi.mocked(accessToken).mockResolvedValue(null);
  });

  it('/login 是独立整页（不带站点导航）', () => {
    renderApp('/login');
    expect(screen.queryByRole('banner')).toBeNull();
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
    expect(
      await screen.findByRole('heading', { name: '对话' }),
    ).toBeInTheDocument();
  });

  it('公开页（/about）不受守卫影响', async () => {
    renderApp('/about');
    expect(
      await screen.findByRole('heading', { name: '关于 beauty moon' }),
    ).toBeInTheDocument();
  });
});
