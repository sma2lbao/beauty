/**
 * W2 R-21:运行详情抽屉契约。
 * - 标题/目标/状态徽章来自 run 查询;
 * - awaiting_approval 状态出现「放行执行/拒绝计划」按钮;
 * - 非终态出现「取消任务」并调用取消 API,终态不再出现;
 * - 关闭按钮触发 onClose 回调。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Plan, Run, RunItem, RunStep, RunTodoItem } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { RunDetail } from './run-detail';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    get: vi.fn(),
    getPlan: vi.fn(),
    steps: vi.fn(),
    items: vi.fn(),
    todos: vi.fn(),
    instructions: vi.fn(),
    retry: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    generatePlan: vi.fn(),
    updatePlan: vi.fn(),
    queueInstruction: vi.fn(),
    resume: vi.fn(),
    cancel: vi.fn(),
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

function renderDetail(
  status: Run['status'],
  onClose = vi.fn(),
  steps: RunStep[] = [],
  todos: RunTodoItem[] = [],
  items: RunItem[] = [],
) {
  vi.mocked(runsApi.get).mockResolvedValue({ ...baseRun, status });
  vi.mocked(runsApi.getPlan).mockResolvedValue(plan);
  vi.mocked(runsApi.instructions).mockResolvedValue([]);
  vi.mocked(runsApi.steps).mockResolvedValue(steps);
  vi.mocked(runsApi.items).mockResolvedValue(items);
  vi.mocked(runsApi.todos).mockResolvedValue({
    todos,
    counts: {
      pending: todos.filter((todo) => todo.status === 'pending').length,
      in_progress: todos.filter((todo) => todo.status === 'in_progress').length,
      completed: todos.filter((todo) => todo.status === 'completed').length,
    },
    revision: todos.length > 0 ? 1 : 0,
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
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

  it('非终态提供取消按钮,点击调用取消 API', async () => {
    vi.mocked(runsApi.cancel).mockResolvedValue({
      ...baseRun,
      status: 'cancelled',
      status_reason: '已取消',
    });
    renderDetail('awaiting_approval');
    const cancel = await screen.findByRole('button', { name: '取消任务' });
    fireEvent.click(cancel);
    await waitFor(() => {
      expect(runsApi.cancel).toHaveBeenCalledWith('run-1');
    });
  });

  it('渲染执行步骤时间线', async () => {
    renderDetail('running', vi.fn(), [
      {
        seq: 2,
        step_id: 'run-1:1:plan:1',
        node: 'plan',
        phase: 'plan',
        attempt: 1,
        round: 1,
        status: 'completed',
        started_at: '2026-10-01T00:00:00Z',
        finished_at: '2026-10-01T00:00:02Z',
        duration_ms: 2000,
      },
    ]);
    expect(await screen.findByTestId('run-step-timeline')).toBeTruthy();
    expect(screen.getByText('执行步骤')).toBeTruthy();
    expect(screen.getByText(/^规划/)).toBeTruthy();
  });

  it('渲染运行项时间线(plan 与 tool 统一投影)', async () => {
    renderDetail(
      'running',
      vi.fn(),
      [],
      [],
      [
        {
          item_id: 'run-1:plan:1',
          thread_id: 'run-1',
          turn_id: 'turn-1',
          kind: 'plan',
          phase: 'completed',
          status: 'completed',
          node: 'plan',
          step_id: 'run-1:1:plan:1',
          attempt: 1,
          title: '执行计划',
          seq: 1,
          started_at: '2026-10-01T00:00:00Z',
          finished_at: '2026-10-01T00:00:02Z',
          duration_ms: 2000,
          data: {
            version: 1,
            supersedes: null,
            content: '1. 改超时配置',
            chars: 12,
          },
        },
        {
          item_id: 'run-1:tool:1',
          thread_id: 'run-1',
          turn_id: 'turn-1',
          kind: 'tool',
          phase: 'completed',
          status: 'completed',
          node: 'execute',
          step_id: 'run-1:1:tool:1',
          attempt: 1,
          title: '读取配置',
          seq: 2,
          started_at: '2026-10-01T00:00:02Z',
          finished_at: '2026-10-01T00:00:03Z',
          duration_ms: 1000,
          data: { name: 'read_file', ok: true, arguments: '{}' },
        },
      ],
    );
    expect(await screen.findByTestId('run-item-timeline')).toBeTruthy();
    expect(screen.getByText('运行项')).toBeTruthy();
    expect(screen.getByText('执行计划')).toBeTruthy();
    expect(screen.getByText('读取配置')).toBeTruthy();
  });

  it('渲染任务清单与完成计数', async () => {
    renderDetail(
      'running',
      vi.fn(),
      [],
      [
        { content: '读取配置', status: 'completed' },
        { content: '实现工具', status: 'in_progress' },
        { content: '补充测试', status: 'pending' },
      ],
    );
    expect(await screen.findByTestId('run-todo-list')).toBeTruthy();
    expect(screen.getByText('任务清单')).toBeTruthy();
    expect(screen.getByText('1/3')).toBeTruthy();
  });

  it('终态不出现取消按钮', async () => {
    renderDetail('completed');
    await waitFor(() => {
      expect(screen.getByText('修登录超时 bug')).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: '取消任务' })).toBeNull();
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
