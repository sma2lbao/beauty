import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AuthResult } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import {
  accountLabel,
  authMode,
  rememberLoginRedirect,
  setStoredToken,
  setStoredTokens,
  signIn,
  signOut,
} from '@/lib/auth';

import { LoginPage } from './login';

vi.mock('@/lib/auth', () => ({
  authMode: vi.fn(() => 'token'),
  tenantId: vi.fn(() => 'demo-tenant'),
  accessToken: vi.fn(async () => null),
  authHeaders: vi.fn(async () => ({})),
  accountLabel: vi.fn(async () => null),
  getStoredToken: vi.fn(() => null),
  getStoredRefreshToken: vi.fn(() => null),
  refreshAccessToken: vi.fn(async () => false),
  setStoredToken: vi.fn(),
  setStoredTokens: vi.fn(),
  rememberLoginRedirect: vi.fn(),
  takeLoginRedirect: vi.fn(() => '/'),
  callbackUri: vi.fn(() => 'http://localhost:3000/callback'),
  signIn: vi.fn(async () => {}),
  signOut: vi.fn(async () => {}),
  handleSignInCallback: vi.fn(async () => {}),
}));

vi.mock('@/lib/account-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/account-api')>('@/lib/account-api');
  return {
    ...actual,
    accountApi: {
      login: vi.fn(),
      register: vi.fn(),
    },
  };
});

const authResult: AuthResult = {
  access_token: 'access-jwt',
  refresh_token: 'refresh-jwt',
  token_type: 'Bearer',
  expires_in: 3600,
  tenant_id: 't-1',
  organization_id: 't-1',
  user_id: 'u-1',
  subject: 'a@b.c',
  role: 'owner',
  session_id: 's-1',
};

function renderLogin(options?: { from?: string }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const initialEntry = options?.from
    ? { pathname: '/login', state: { from: { pathname: options.from } } }
    : '/login';
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/runs" element={<div>运行控制台占位</div>} />
          <Route path="/" element={<div>对话页占位</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authMode).mockReturnValue('token');
  vi.mocked(accountLabel).mockResolvedValue(null);
});

describe('LoginPage', () => {
  it('token 模式:密码登录成功后保存双令牌并回到来路', async () => {
    vi.mocked(accountApi.login).mockResolvedValue(authResult);
    renderLogin({ from: '/runs' });

    fireEvent.change(screen.getByLabelText('邮箱'), {
      target: { value: 'a@b.c' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'hunter2ok' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(accountApi.login).toHaveBeenCalledWith({
        email: 'a@b.c',
        password: 'hunter2ok',
      });
    });
    expect(setStoredTokens).toHaveBeenCalledWith(
      'access-jwt',
      'refresh-jwt',
      'a@b.c',
    );
    expect(await screen.findByText('运行控制台占位')).toBeInTheDocument();
  });

  it('切到注册:提交注册表单建账号', async () => {
    vi.mocked(accountApi.register).mockResolvedValue(authResult);
    renderLogin({ from: '/runs' });

    fireEvent.click(screen.getByRole('tab', { name: '注册' }));
    fireEvent.change(screen.getByLabelText('邮箱'), {
      target: { value: 'a@b.c' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'hunter2ok' },
    });
    fireEvent.change(screen.getByLabelText(/显示名/), {
      target: { value: 'Alice' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建账号' }));

    await waitFor(() => {
      expect(accountApi.register).toHaveBeenCalledWith({
        email: 'a@b.c',
        password: 'hunter2ok',
        display_name: 'Alice',
      });
    });
    expect(await screen.findByText('运行控制台占位')).toBeInTheDocument();
  });

  it('登录失败时展示错误,不离开登录页', async () => {
    vi.mocked(accountApi.login).mockRejectedValue(
      new Error('邮箱或密码不正确'),
    );
    renderLogin();

    fireEvent.change(screen.getByLabelText('邮箱'), {
      target: { value: 'a@b.c' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'wrong-pass' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText('邮箱或密码不正确')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('粘贴 access token 也可进入', async () => {
    renderLogin();

    fireEvent.change(screen.getByLabelText('access token'), {
      target: { value: 'pasted-jwt' },
    });
    fireEvent.click(screen.getByRole('button', { name: '用令牌进入' }));

    await waitFor(() => {
      expect(setStoredToken).toHaveBeenCalledWith('pasted-jwt');
    });
    expect(await screen.findByText('对话页占位')).toBeInTheDocument();
  });

  it('logto 模式:未登录时跳 IdP 并记下来路', async () => {
    vi.mocked(authMode).mockReturnValue('logto');
    renderLogin({ from: '/knowledge' });

    fireEvent.click(screen.getByRole('button', { name: '使用 Logto 登录' }));

    await waitFor(() => {
      expect(rememberLoginRedirect).toHaveBeenCalledWith('/knowledge');
      expect(signIn).toHaveBeenCalled();
    });
  });

  it('logto 模式:已登录显示账号与进入/退出操作', async () => {
    vi.mocked(authMode).mockReturnValue('logto');
    vi.mocked(accountLabel).mockResolvedValue('a@b.c');
    renderLogin();

    expect(await screen.findByText('已登录:a@b.c')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '进入控制台' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '退出登录' }),
    ).toBeInTheDocument();
  });

  it('退出登录后回到登录表单', async () => {
    // 首次查询返回已登录,失效重查返回未登录
    vi.mocked(accountLabel).mockResolvedValueOnce('手动令牌');
    renderLogin();

    expect(await screen.findByText('已登录:手动令牌')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));

    await waitFor(() => {
      expect(signOut).toHaveBeenCalled();
    });
    expect(
      await screen.findByRole('button', { name: '登录' }),
    ).toBeInTheDocument();
  });
});
