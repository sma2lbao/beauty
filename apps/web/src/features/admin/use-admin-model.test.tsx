import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Account } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import { adminApi, type AdminAccount, type AuditLogList } from '@/lib/admin-api';
import { ApiError } from '@/lib/http';

import { ADMIN_PAGE_SIZE, useAdminModel } from './use-admin-model';

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

const ADMIN_ID = 'a'.repeat(32);

const me: Account = {
  id: ADMIN_ID,
  email: 'admin@example.com',
  display_name: '运营',
  status: 'active',
  role: 'admin',
  created_at: '2026-10-13T00:00:00+00:00',
};

const row: AdminAccount = {
  id: 'b'.repeat(32),
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
};

const emptyAudit: AuditLogList = { items: [], total: 0 };

function renderModel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useAdminModel(), { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(accountApi.me).mockResolvedValue(me);
  vi.mocked(adminApi.listAccounts).mockResolvedValue({ items: [row], total: 1 });
  vi.mocked(adminApi.listAuditLogs).mockResolvedValue(emptyAudit);
  vi.mocked(adminApi.updateRole).mockResolvedValue({ ...row, role: 'admin' });
});

describe('useAdminModel 权限闸门', () => {
  it('管理员:先确认 me,再按默认分页拉账号', async () => {
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('ok'));
    expect(adminApi.listAccounts).toHaveBeenCalledWith({
      q: undefined,
      status: undefined,
      limit: ADMIN_PAGE_SIZE,
      offset: 0,
    });
  });

  it('普通用户:整页无权限且不查询账号', async () => {
    vi.mocked(accountApi.me).mockResolvedValue({ ...me, role: 'user' });
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('forbidden'));
    expect(adminApi.listAccounts).not.toHaveBeenCalled();
    expect(adminApi.listAuditLogs).not.toHaveBeenCalled();
  });

  it('me 401 → 登录失效', async () => {
    vi.mocked(accountApi.me).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('unauthorized'));
  });

  it('角色被撤销后账号查询 403 → 提升为无权限', async () => {
    vi.mocked(adminApi.listAccounts).mockRejectedValue(
      new ApiError(403, 'admin.required', '需要管理员权限'),
    );
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('forbidden'));
  });
});

describe('useAdminModel 账号列表', () => {
  it('搜索词 trim、状态与翻页都进查询参数', async () => {
    const { result } = renderModel();
    await waitFor(() => expect(result.current.access).toBe('ok'));

    act(() => {
      result.current.accounts.applyFilters({ query: '  ops ', status: 'disabled' });
    });
    await waitFor(() =>
      expect(adminApi.listAccounts).toHaveBeenLastCalledWith({
        q: 'ops',
        status: 'disabled',
        limit: ADMIN_PAGE_SIZE,
        offset: 0,
      }),
    );

    act(() => {
      result.current.accounts.setOffset(ADMIN_PAGE_SIZE);
    });
    await waitFor(() =>
      expect(adminApi.listAccounts).toHaveBeenLastCalledWith({
        q: 'ops',
        status: 'disabled',
        limit: ADMIN_PAGE_SIZE,
        offset: ADMIN_PAGE_SIZE,
      }),
    );
  });

  it('写操作成功后刷新账号与审计查询', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useAdminModel(), { wrapper });
    await waitFor(() => expect(result.current.access).toBe('ok'));
    const before = vi.mocked(adminApi.listAccounts).mock.calls.length;

    await act(() =>
      result.current.roleMutation.mutateAsync({
        accountId: row.id,
        role: 'admin',
      }),
    );

    expect(adminApi.updateRole).toHaveBeenCalledWith(row.id, { role: 'admin' });
    await waitFor(() =>
      expect(vi.mocked(adminApi.listAccounts).mock.calls.length).toBeGreaterThan(
        before,
      ),
    );
  });
});

describe('useAdminModel 审计', () => {
  it('切到审计页签才查询,过滤条件提交后 trim 并回到第一页', async () => {
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue({
      items: [
        {
          id: 'log-1',
          actor_account_id: ADMIN_ID,
          actor_email: 'admin@example.com',
          action: 'account.role.update',
          target_type: 'account',
          target_id: row.id,
          status: 'success',
          detail: { before: 'user', after: 'admin' },
          request_id: 'req-1',
          ip: '127.0.0.1',
          user_agent: null,
          created_at: '2026-10-15T00:00:00+00:00',
        },
      ],
      total: 1,
    });
    const { result } = renderModel();
    await waitFor(() => expect(result.current.access).toBe('ok'));
    expect(adminApi.listAuditLogs).not.toHaveBeenCalled();

    act(() => {
      result.current.setTab('audit');
    });
    await waitFor(() =>
      expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
        limit: ADMIN_PAGE_SIZE,
        offset: 0,
        actor_account_id: undefined,
        action: undefined,
        target_id: undefined,
      }),
    );

    act(() => {
      result.current.audit.applyFilters({
        action: ' account.role.update ',
        actorAccountId: ADMIN_ID,
        targetId: row.id,
      });
    });
    await waitFor(() =>
      expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
        limit: ADMIN_PAGE_SIZE,
        offset: 0,
        actor_account_id: ADMIN_ID,
        action: 'account.role.update',
        target_id: row.id,
      }),
    );
  });
});

describe('useAdminModel 页签', () => {
  it('默认账号页签,切到成本页签不触发审计查询', async () => {
    const { result } = renderModel();
    await waitFor(() => expect(result.current.access).toBe('ok'));
    expect(result.current.tab).toBe('accounts');

    act(() => {
      result.current.setTab('cost');
    });

    await waitFor(() => expect(result.current.tab).toBe('cost'));
    expect(adminApi.listAuditLogs).not.toHaveBeenCalled();
  });
});
