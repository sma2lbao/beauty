import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Account, Session } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import { approvalApi } from '@/lib/approval-api';
import { hooksApi } from '@/lib/hooks-api';
import { ApiError } from '@/lib/http';

import { SettingsPage } from './settings';

vi.mock('@/lib/account-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/account-api')>(
      '@/lib/account-api',
    );
  return {
    ...actual,
    accountApi: {
      me: vi.fn(),
      updateMe: vi.fn(),
      sessions: vi.fn(),
      addSession: vi.fn(),
      revokeSession: vi.fn(),
    },
  };
});

vi.mock('@/lib/approval-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/approval-api')>(
      '@/lib/approval-api',
    );
  return {
    ...actual,
    approvalApi: { list: vi.fn(), replace: vi.fn() },
  };
});

vi.mock('@/lib/hooks-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/hooks-api')>(
      '@/lib/hooks-api',
    );
  return {
    ...actual,
    hooksApi: {
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      rotate: vi.fn(),
    },
  };
});

vi.mock('@/features/auth/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

const account: Account = {
  id: 'a'.repeat(32),
  email: 'me@example.com',
  display_name: '小月',
  status: 'active',
  role: 'user',
  created_at: '2026-10-13T00:00:00+00:00',
};

const session: Session = {
  id: 's1',
  device: 'MacBook / Chrome',
  status: 'active',
  last_seen_at: null,
  created_at: '2026-10-13T00:00:00+00:00',
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // 清掉上一个用例的调用历史:否则 not.toHaveBeenCalled 之类的断言会跨用例误判
  vi.clearAllMocks();
  vi.mocked(accountApi.me).mockResolvedValue(account);
  vi.mocked(accountApi.sessions).mockResolvedValue([session]);
  vi.mocked(approvalApi.list).mockResolvedValue([]);
  vi.mocked(hooksApi.list).mockResolvedValue([]);
});

describe('SettingsPage', () => {
  it('展示账号资料与设备会话', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('邮箱')).toHaveProperty(
        'value',
        'me@example.com',
      );
    });
    expect(screen.getByLabelText('显示名')).toHaveProperty('value', '小月');
    expect(screen.getByText('正常')).toBeTruthy();
    expect(screen.getByText('MacBook / Chrome')).toBeTruthy();
  });

  it('展示工具审批策略面板与空表说明', async () => {
    renderPage();

    expect(await screen.findByText('工具审批策略')).toBeTruthy();
    expect(
      await screen.findByText(/还没有规则:默认按风险等级处理/),
    ).toBeTruthy();
  });

  it('展示自动化 hooks 面板与空态说明', async () => {
    renderPage();

    expect(await screen.findByText('自动化 hooks')).toBeTruthy();
    expect(await screen.findByText(/还没有 hook/)).toBeTruthy();
  });

  it('保存显示名时调用 PATCH /me', async () => {
    vi.mocked(accountApi.updateMe).mockResolvedValue({
      ...account,
      display_name: '新月',
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('显示名')).toHaveProperty('value', '小月');
    });

    fireEvent.change(screen.getByLabelText('显示名'), {
      target: { value: '新月' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(accountApi.updateMe).toHaveBeenCalledWith({
        display_name: '新月',
      });
    });
  });

  it('登记新会话时只提交设备', async () => {
    vi.mocked(accountApi.addSession).mockResolvedValue(session);

    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('设备')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('设备'), {
      target: { value: 'iPhone / Safari' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登记会话' }));

    await waitFor(() => {
      expect(accountApi.addSession).toHaveBeenCalledWith({
        device: 'iPhone / Safari',
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText('设备')).toHaveProperty('value', '');
    });
  });

  it('吊销会话调用吊销接口', async () => {
    vi.mocked(accountApi.revokeSession).mockResolvedValue({
      ...session,
      status: 'revoked',
    });

    renderPage();
    await screen.findByText('MacBook / Chrome');

    fireEvent.click(screen.getByRole('button', { name: '吊销' }));

    await waitFor(() => {
      expect(accountApi.revokeSession).toHaveBeenCalledWith('s1');
    });
  });

  it('401 时提示先去鉴权', async () => {
    vi.mocked(accountApi.me).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );

    renderPage();

    expect(
      await screen.findByText(/未登录或令牌失效:先在上方面板完成鉴权/),
    ).toBeTruthy();
  });
});
