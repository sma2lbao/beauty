/**
 * P1-C:新建任务表单的本地校验与提交载荷。
 *
 * 表单只在 model 里做「校验 → 组 body」,弹窗只管展示;这里直接测 model,
 * 断言留空超时不落键、优先级按整数提交、非法输入不发请求。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { resetRunBoard } from '@/stores/run-board-store';

import { useRunsModel, validateRunCreate } from './use-runs-model';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
  isUnauthorized: () => false,
}));

const createdRun: Run = {
  id: 'run-new',
  title: '整理本周文档成周报',
  goal: '汇总本周文档,生成周报草稿',
  status: 'pending',
  rounds: 0,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

function renderModel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useRunsModel(), { wrapper });
}

describe('validateRunCreate', () => {
  it('留空超时/默认优先级合法', () => {
    expect(validateRunCreate({ timeoutSeconds: '', priority: '0' })).toBeNull();
    expect(validateRunCreate({ timeoutSeconds: '0', priority: '-100' })).toBeNull();
    expect(validateRunCreate({ timeoutSeconds: '604800', priority: '100' })).toBeNull();
  });

  it('拒绝非整数/越界输入', () => {
    expect(validateRunCreate({ timeoutSeconds: 'abc', priority: '0' })).toMatch(
      /整数秒/,
    );
    expect(validateRunCreate({ timeoutSeconds: '604801', priority: '0' })).toMatch(
      /604800/,
    );
    expect(validateRunCreate({ timeoutSeconds: '', priority: '101' })).toMatch(
      /-100\.\.100/,
    );
    expect(validateRunCreate({ timeoutSeconds: '', priority: '1.5' })).toMatch(
      /-100\.\.100/,
    );
  });
});

describe('useRunsModel 新建任务', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRunBoard();
    vi.mocked(runsApi.list).mockResolvedValue([]);
    vi.mocked(runsApi.create).mockResolvedValue(createdRun);
  });

  it('提交超时秒数与优先级', async () => {
    const { result } = renderModel();
    act(() => {
      result.current.setTimeoutSeconds('120');
      result.current.setPriority('-5');
    });
    act(() => {
      result.current.submitCreate();
    });

    await waitFor(() => expect(runsApi.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(runsApi.create).mock.calls[0]?.[0]).toMatchObject({
      timeout_seconds: 120,
      priority: -5,
    });
  });

  it('超时留空时不带 timeout_seconds(走平台默认)', async () => {
    const { result } = renderModel();
    act(() => {
      result.current.submitCreate();
    });

    await waitFor(() => expect(runsApi.create).toHaveBeenCalledTimes(1));
    const body = vi.mocked(runsApi.create).mock.calls[0]?.[0];
    expect(body).not.toHaveProperty('timeout_seconds');
    expect(body?.priority).toBe(0);
  });

  it('非法优先级不发请求,修正后可提交', async () => {
    const { result } = renderModel();
    act(() => {
      result.current.setPriority('101');
    });
    act(() => {
      result.current.submitCreate();
    });

    expect(result.current.createError).toMatch(/-100\.\.100/);
    expect(runsApi.create).not.toHaveBeenCalled();

    act(() => {
      result.current.setPriority('10');
    });
    act(() => {
      result.current.submitCreate();
    });
    await waitFor(() => expect(runsApi.create).toHaveBeenCalledTimes(1));
    expect(result.current.createError).toBeNull();
  });
});
