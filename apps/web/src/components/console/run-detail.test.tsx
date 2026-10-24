/**
 * W2 R-21:运行详情抽屉契约。
 * - 标题/目标/状态徽章来自 run 查询;
 * - awaiting_approval 状态出现「放行执行/拒绝计划」按钮;
 * - 关闭按钮触发 onClose 回调。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Plan, Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { RunDetail } from './run-detail';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    get: vi.fn(),
    getPlan: vi.fn(),
    instructions: vi.fn(),
    retry: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    generatePlan: vi.fn(),
    updatePlan: vi.fn(),
    queueInstruction: vi.fn(),
    resume: vi.fn(),
  },
}));

vi.mock('@/components/runs/use-run-events', () => ({
  useRunEvents: () => ({ events: [], connected: true }),
}));

const baseRun: Run = {
  id: 'run-1',
  title: '修登录超时 bug',
  goal: '修复登录 30s 超时',
  status: 'awaiting_approval',
  rounds: 1,
  attempts: 1,
  acceptance_criteria: ['登录不再超时'],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

const plan: Plan = {
  run_id: 'run-1',
  status: 'awaiting_approval',
  plan_markdown: '1. 改超时配置',
};

function renderDetail(status: Run['status'], onClose = vi.fn()) {
  vi.mocked(runsApi.get).mockResolvedValue({ ...baseRun, status });
  vi.mocked(runsApi.getPlan).mockResolvedValue(plan);
  vi.mocked(runsApi.instructions).mockResolvedValue([]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunDetail runId="run-1" onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe('RunDetail', () => {
  it('渲染 run 标题与目标', async () => {
    renderDetail('awaiting_approval');
    await waitFor(() => {
      expect(screen.getByText('修登录超时 bug')).toBeTruthy();
    });
    expect(screen.getByText('修复登录 30s 超时')).toBeTruthy();
  });

  it('awaiting_approval 状态提供放行与拒绝按钮', async () => {
    renderDetail('awaiting_approval');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '放行执行' })).toBeTruthy();
    });
    expect(screen.getByRole('button', { name: '拒绝计划' })).toBeTruthy();
  });

  it('非审批状态不出现审批按钮', async () => {
    renderDetail('completed');
    await waitFor(() => {
      expect(screen.getByText('修登录超时 bug')).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: '放行执行' })).toBeNull();
  });

  it('关闭按钮触发 onClose', async () => {
    const onClose = vi.fn();
    renderDetail('completed', onClose);
    await waitFor(() => {
      expect(screen.getByText('修登录超时 bug')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: '关闭详情' }));
    expect(onClose).toHaveBeenCalled();
  });
});
