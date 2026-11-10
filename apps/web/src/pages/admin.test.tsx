import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Account } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import {
  adminApi,
  type AdminAccount,
  type AdminCostSummary,
  type AuditLogRead,
} from '@/lib/admin-api';
import { ApiError } from '@/lib/http';

import { AdminPage } from './admin';

vi.mock('@/lib/admin-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/admin-api')>('@/lib/admin-api');
  return {
    ...actual,
    adminApi: {
      listAccounts: vi.fn(),
      getAccount: vi.fn(),
      updateQuota: vi.fn(),
      updateRole: vi.fn(),
      updateStatus: vi.fn(),
      listAuditLogs: vi.fn(),
      costSummary: vi.fn(),
    },
  };
});

vi.mock('@/lib/account-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/account-api')>(
      '@/lib/account-api',
    );
  return { ...actual, accountApi: { me: vi.fn() } };
});

const SELF_ID = 'a'.repeat(32);
const OTHER_ID = 'b'.repeat(32);

const me: Account = {
  id: SELF_ID,
  email: 'admin@example.com',
  display_name: '运营',
  status: 'active',
  role: 'admin',
  created_at: '2026-10-13T00:00:00+00:00',
};

function makeAccount(overrides: Partial<AdminAccount>): AdminAccount {
  return {
    id: OTHER_ID,
    email: 'ops@example.com',
    display_name: '值班',
    status: 'active',
    role: 'user',
    quota: { monthly_tokens: 1000, max_concurrent_runs: 2 },
    created_at: '2026-10-14T00:00:00+00:00',
    usage: {
      month_tokens: 1234,
      month_events: 5,
      month_cost_usd: 0.42,
      unpriced_events: 0,
    },
    ...overrides,
  };
}

const self = makeAccount({
  id: SELF_ID,
  email: 'admin@example.com',
  display_name: '运营',
  role: 'admin',
  quota: { monthly_tokens: 50000, max_concurrent_runs: 8 },
  usage: {
    month_tokens: 999,
    month_events: 3,
    month_cost_usd: 0.11,
    unpriced_events: 0,
  },
});
const other = makeAccount({});

const auditLog: AuditLogRead = {
  id: 'log-1',
  actor_account_id: SELF_ID,
  actor_email: 'admin@example.com',
  action: 'account.role.update',
  target_type: 'account',
  target_id: OTHER_ID,
  status: 'success',
  detail: { before: 'user', after: 'admin' },
  request_id: 'req-1',
  ip: '127.0.0.1',
  user_agent: null,
  created_at: '2026-10-15T00:00:00+00:00',
};

const costSummary: AdminCostSummary = {
  days: 30,
  since: '2026-10-12T00:00:00Z',
  totals: {
    events: 120,
    tokens: 45000,
    cost_usd: 1.23,
    unpriced_events: 4,
    platform_events: 100,
    byok_events: 20,
  },
  cost_by_source: [
    { source: 'platform', events: 100, cost_usd: 1.1 },
    { source: 'byok', events: 20, cost_usd: 0.13 },
  ],
  by_day: [
    {
      date: '2026-10-12',
      events: 2,
      tokens: 100,
      cost_usd: 0.2,
      unpriced_events: 0,
    },
    {
      date: '2026-10-13',
      events: 3,
      tokens: 150,
      cost_usd: 0.4,
      unpriced_events: 1,
    },
  ],
  by_model: [
    { model: 'gpt-x', events: 80, tokens: 30000, cost_usd: 0.9, unpriced_events: 0 },
  ],
  top_accounts: [
    {
      account_id: OTHER_ID,
      email: 'ops@example.com',
      events: 40,
      tokens: 12000,
      cost_usd: 0.5,
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AdminPage />
    </QueryClientProvider>,
  );
}

function lastAccountsArgs() {
  return vi.mocked(adminApi.listAccounts).mock.calls.at(-1)?.[0];
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(accountApi.me).mockResolvedValue(me);
  vi.mocked(adminApi.listAccounts).mockResolvedValue({
    items: [self, other],
    total: 2,
  });
  vi.mocked(adminApi.listAuditLogs).mockResolvedValue({
    items: [auditLog],
    total: 1,
  });
  vi.mocked(adminApi.updateRole).mockResolvedValue({ ...other, role: 'admin' });
  vi.mocked(adminApi.updateStatus).mockResolvedValue({
    ...other,
    status: 'disabled',
  });
  vi.mocked(adminApi.updateQuota).mockResolvedValue(other);
  vi.mocked(adminApi.costSummary).mockResolvedValue(costSummary);
});

describe('AdminPage 访问控制', () => {
  it('管理员看到管理台、账号表格与三个页签', async () => {
    renderPage();

    expect(
      await screen.findByRole('heading', { level: 1, name: '管理台' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('平台运营 · 跨账号治理,所有写操作留痕'),
    ).toBeInTheDocument();
    expect(await screen.findByText('ops@example.com')).toBeInTheDocument();
    expect(screen.getByText('admin@example.com')).toBeInTheDocument();
    // 本月 tokens 千分位
    expect(screen.getByText('1,234')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '账号' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: '审计日志' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '成本' })).toBeInTheDocument();
  });

  it('非管理员直接访问显示无权限面板,且不查询账号', async () => {
    vi.mocked(accountApi.me).mockResolvedValue({ ...me, role: 'user' });
    renderPage();

    expect(await screen.findByText('无管理员权限')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(adminApi.listAccounts).not.toHaveBeenCalled();
  });

  it('登录失效显示 401 面板而不是白屏', async () => {
    vi.mocked(accountApi.me).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );
    renderPage();

    expect(await screen.findByText('登录失效')).toBeInTheDocument();
    expect(screen.queryByText('无管理员权限')).not.toBeInTheDocument();
  });

  it('自己的账号不允许改角色/停用', async () => {
    renderPage();

    expect(
      await screen.findByRole('button', { name: '降为普通用户' }),
    ).toBeDisabled();
    // 两行都渲染「停用」,第一行是自己的账号
    const statusButtons = screen.getAllByRole('button', { name: '停用' });
    expect(statusButtons).toHaveLength(2);
    expect(statusButtons[0]).toBeDisabled();
    expect(statusButtons[1]).toBeEnabled();
  });
});

describe('AdminPage 账号治理', () => {
  it('搜索与状态筛选进入查询参数,空过滤条件被省略', async () => {
    renderPage();
    await screen.findByText('ops@example.com');
    expect(lastAccountsArgs()).toEqual({
      q: undefined,
      status: undefined,
      limit: 20,
      offset: 0,
    });

    fireEvent.change(screen.getByLabelText('搜索账号'), {
      target: { value: 'ops' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() =>
      expect(lastAccountsArgs()).toEqual({
        q: 'ops',
        status: undefined,
        limit: 20,
        offset: 0,
      }),
    );

    fireEvent.change(screen.getByLabelText('状态'), {
      target: { value: 'disabled' },
    });
    await waitFor(() =>
      expect(lastAccountsArgs()).toEqual({
        q: 'ops',
        status: 'disabled',
        limit: 20,
        offset: 0,
      }),
    );
  });

  it('切换角色:确认弹窗写清影响,成功后调用 API 并刷新', async () => {
    renderPage();
    await screen.findByText('ops@example.com');

    fireEvent.click(screen.getByRole('button', { name: '设为管理员' }));
    expect(screen.getByText('修改角色')).toBeInTheDocument();
    expect(
      screen.getByText(
        'ops@example.com:设为管理员后可访问管理台并跨账号治理。',
      ),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '确认设为管理员' }),
    );
    await waitFor(() =>
      expect(adminApi.updateRole).toHaveBeenCalledWith(OTHER_ID, {
        role: 'admin',
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText('修改角色')).not.toBeInTheDocument(),
    );
  });

  it('停用账号:确认后提交 status=disabled', async () => {
    renderPage();
    await screen.findByText('ops@example.com');

    // 行按自建账号在前,第二个「停用」是目标账号
    fireEvent.click(screen.getAllByRole('button', { name: '停用' })[1]);
    // 弹窗标题与确认按钮同名:两处都在才说明确认弹窗真的开了
    expect(screen.getAllByText('停用账号').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '停用账号' }));
    await waitFor(() =>
      expect(adminApi.updateStatus).toHaveBeenCalledWith(OTHER_ID, {
        status: 'disabled',
      }),
    );
  });

  it('编辑配额:清空输入按「不限」提交 null', async () => {
    renderPage();
    await screen.findByText('ops@example.com');

    fireEvent.click(
      screen.getAllByRole('button', { name: '编辑配额' })[1],
    );
    fireEvent.change(screen.getByLabelText('每月 tokens(留空表示不限)'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存配额' }));

    await waitFor(() =>
      expect(adminApi.updateQuota).toHaveBeenCalledWith(OTHER_ID, {
        monthly_tokens: null,
        max_concurrent_runs: 2,
      }),
    );
  });

  it('读取失败显示错误与重试,不白屏', async () => {
    vi.mocked(adminApi.listAccounts).mockRejectedValue(
      new ApiError(500, 'internal', '服务开小差了'),
    );
    renderPage();

    expect(
      await screen.findByText('读取账号失败:服务开小差了'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(adminApi.listAccounts).toHaveBeenCalledTimes(2));
  });

  it('没有匹配账号时显示空态', async () => {
    vi.mocked(adminApi.listAccounts).mockResolvedValue({
      items: [],
      total: 0,
    });
    renderPage();

    expect(await screen.findByText('没有匹配的账号')).toBeInTheDocument();
  });
});

describe('AdminPage 审计', () => {
  it('审计页签按过滤条件查询,详情可展开 detail JSON', async () => {
    renderPage();
    await screen.findByText('ops@example.com');

    fireEvent.click(screen.getByRole('tab', { name: '审计日志' }));
    await waitFor(() =>
      expect(adminApi.listAuditLogs).toHaveBeenCalledWith({
        limit: 20,
        offset: 0,
        actor_account_id: undefined,
        action: undefined,
        target_id: undefined,
      }),
    );
    expect(await screen.findByText('account.role.update')).toBeInTheDocument();
    expect(screen.getByText('account:' + OTHER_ID)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('action'), {
      target: { value: ' account.role.update ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() =>
      expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
        limit: 20,
        offset: 0,
        actor_account_id: undefined,
        action: 'account.role.update',
        target_id: undefined,
      }),
    );

    expect(
      await screen.findByText('account.role.update'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(screen.getByText(/"before": "user"/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('审计为空时显示空态', async () => {
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue({
      items: [],
      total: 0,
    });
    renderPage();
    await screen.findByText('ops@example.com');

    fireEvent.click(screen.getByRole('tab', { name: '审计日志' }));
    expect(await screen.findByText('没有审计记录')).toBeInTheDocument();
  });
});
describe('AdminPage 成本', () => {
  it('切到成本页签按默认 30 天查询,切换范围重新查询', async () => {
    renderPage();
    await screen.findByText('ops@example.com');
    expect(adminApi.costSummary).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: '成本' }));
    await waitFor(() => expect(adminApi.costSummary).toHaveBeenCalledWith(30));
    expect(await screen.findByTestId('admin-cost-panel')).toBeInTheDocument();
    expect(screen.getByText('$1.23')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '最近 7 天' }));
    await waitFor(() =>
      expect(adminApi.costSummary).toHaveBeenLastCalledWith(7),
    );
  });

  it('成本查询 403 沿用「无管理员权限」面板', async () => {
    vi.mocked(adminApi.costSummary).mockRejectedValue(
      new ApiError(403, 'admin.required', '需要管理员权限'),
    );
    renderPage();
    await screen.findByText('ops@example.com');

    fireEvent.click(screen.getByRole('tab', { name: '成本' }));
    expect(await screen.findByText('无管理员权限')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-cost-panel')).not.toBeInTheDocument();
  });
});
