import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Member, Organization, Session } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import { ApiError } from '@/lib/http';

import { SettingsPage } from './settings';

vi.mock('@/lib/account-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/account-api')>('@/lib/account-api');
  return {
    ...actual,
    accountApi: {
      organization: vi.fn(),
      updateOrganization: vi.fn(),
      members: vi.fn(),
      addMember: vi.fn(),
      updateMember: vi.fn(),
      sessions: vi.fn(),
      addSession: vi.fn(),
      revokeSession: vi.fn(),
    },
  };
});

vi.mock('@/features/auth/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

const organization: Organization = {
  id: 'org-1',
  name: '示例科技',
  plan: 'team',
  quota: { seats: 20 },
  created_at: '2026-10-13T00:00:00+00:00',
};

const member: Member = {
  id: 'u1',
  subject: 'idp|u1',
  email: 'u1@example.com',
  display_name: '小月',
  role: 'owner',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

const session: Session = {
  id: 's1',
  user_id: 'u1',
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
  vi.mocked(accountApi.organization).mockResolvedValue(organization);
  vi.mocked(accountApi.members).mockResolvedValue([member]);
  vi.mocked(accountApi.sessions).mockResolvedValue([session]);
});

describe('SettingsPage', () => {
  it('展示组织档案、成员与会话', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('组织名称')).toHaveProperty('value', '示例科技');
    });
    expect(screen.getByLabelText('套餐')).toHaveProperty('value', 'team');
    expect(screen.getByLabelText('配额(JSON)')).toHaveProperty(
      'value',
      JSON.stringify({ seats: 20 }),
    );
    expect(await screen.findAllByText('小月')).not.toHaveLength(0);
    expect(screen.getByText('MacBook / Chrome')).toBeTruthy();
    expect(screen.getByText(/共 1 人/)).toBeTruthy();
  });

  it('保存组织档案时带上编辑后的值', async () => {
    vi.mocked(accountApi.updateOrganization).mockResolvedValue({
      ...organization,
      name: '示例科技(新)',
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('组织名称')).toHaveProperty('value', '示例科技');
    });

    fireEvent.change(screen.getByLabelText('组织名称'), {
      target: { value: '示例科技(新)' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(accountApi.updateOrganization).toHaveBeenCalledWith({
        name: '示例科技(新)',
        plan: 'team',
        quota: { seats: 20 },
      });
    });
  });

  it('配额不是合法 JSON 时报错且不发请求', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('配额(JSON)')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('配额(JSON)'), {
      target: { value: '{oops' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(await screen.findByText(/配额必须是合法 JSON/)).toBeTruthy();
    expect(accountApi.updateOrganization).not.toHaveBeenCalled();
  });

  it('添加成员后清空表单并刷新列表', async () => {
    vi.mocked(accountApi.addMember).mockResolvedValue(member);

    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('IdP subject')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('IdP subject'), {
      target: { value: 'idp|u2' },
    });
    fireEvent.change(screen.getByLabelText('邮箱'), {
      target: { value: 'u2@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加成员' }));

    await waitFor(() => {
      expect(accountApi.addMember).toHaveBeenCalledWith({
        subject: 'idp|u2',
        email: 'u2@example.com',
        display_name: null,
        role: 'member',
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText('IdP subject')).toHaveProperty('value', '');
    });
  });

  it('切换角色调用更新接口', async () => {
    vi.mocked(accountApi.updateMember).mockResolvedValue({
      ...member,
      role: 'member',
    });

    renderPage();
    await screen.findByText(/u1@example\.com · idp\|u1/);

    fireEvent.change(screen.getByLabelText('u1@example.com 的角色'), {
      target: { value: 'member' },
    });

    await waitFor(() => {
      expect(accountApi.updateMember).toHaveBeenCalledWith('u1', {
        role: 'member',
      });
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
    vi.mocked(accountApi.organization).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );

    renderPage();

    expect(
      await screen.findByText(/未登录或令牌失效:先在上方面板完成鉴权/),
    ).toBeTruthy();
  });
});
