import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { adminApi, type AdminCostSummary } from '@/lib/admin-api';
import { ApiError } from '@/lib/http';

import {
  ADMIN_COST_DEFAULT_RANGE,
  ADMIN_COST_RANGES,
  useAdminCostModel,
} from './use-admin-cost-model';

vi.mock('@/lib/admin-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/admin-api')>('@/lib/admin-api');
  return { ...actual, adminApi: { costSummary: vi.fn() } };
});

const summary: AdminCostSummary = {
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
  ],
  by_model: [
    { model: 'gpt-x', events: 80, tokens: 30000, cost_usd: 0.9, unpriced_events: 0 },
  ],
  top_accounts: [
    {
      account_id: 'b'.repeat(32),
      email: 'ops@example.com',
      events: 40,
      tokens: 12000,
      cost_usd: 0.5,
    },
  ],
};

function renderModel(initialProps: { enabled: boolean } = { enabled: true }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(
    ({ enabled }: { enabled: boolean }) => useAdminCostModel({ enabled }),
    { wrapper, initialProps },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(adminApi.costSummary).mockResolvedValue(summary);
});

describe('useAdminCostModel 范围与查询', () => {
  it('契约范围是 7/30/90,默认 30', () => {
    expect(ADMIN_COST_RANGES).toEqual([7, 30, 90]);
    expect(ADMIN_COST_DEFAULT_RANGE).toBe(30);
  });

  it('页签未启用时不查询;启用后按默认 30 天查询', async () => {
    const { result, rerender } = renderModel({ enabled: false });

    expect(adminApi.costSummary).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(true);

    rerender({ enabled: true });
    await waitFor(() => expect(result.current.data).toEqual(summary));
    expect(adminApi.costSummary).toHaveBeenCalledWith(30);
    expect(result.current.access).toBeNull();
  });

  it('切换 7/90 天会按新窗口重新查询', async () => {
    const { result } = renderModel();
    await waitFor(() => expect(result.current.data).toEqual(summary));

    act(() => {
      result.current.setRange(7);
    });
    await waitFor(() =>
      expect(adminApi.costSummary).toHaveBeenLastCalledWith(7),
    );
    expect(result.current.range).toBe(7);

    act(() => {
      result.current.setRange(90);
    });
    await waitFor(() =>
      expect(adminApi.costSummary).toHaveBeenLastCalledWith(90),
    );
  });
});

describe('useAdminCostModel 错误口径', () => {
  it('403 → forbidden(整页闸门)', async () => {
    vi.mocked(adminApi.costSummary).mockRejectedValue(
      new ApiError(403, 'admin.required', '需要管理员权限'),
    );
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('forbidden'));
    expect(result.current.error).toBeInstanceOf(ApiError);
  });

  it('401 → unauthorized', async () => {
    vi.mocked(adminApi.costSummary).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );
    const { result } = renderModel();

    await waitFor(() => expect(result.current.access).toBe('unauthorized'));
  });

  it('其他错误留在面板内:access 为 null 且保留错误', async () => {
    vi.mocked(adminApi.costSummary).mockRejectedValue(
      new ApiError(500, 'internal_error', '服务开小差了'),
    );
    const { result } = renderModel();

    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
    expect(result.current.access).toBeNull();
    expect((result.current.error as Error).message).toBe('服务开小差了');
  });
});
