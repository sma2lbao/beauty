/**
 * W2 R-21:运行详情抽屉契约。
 * - 标题/目标/状态徽章来自 run 查询;
 * - awaiting_approval 状态出现「放行执行/拒绝计划」按钮;
 * - 非终态出现「取消任务」并调用取消 API,终态不再出现;
 * - 关闭按钮触发 onClose 回调。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type {
  Plan,
  Run,
  RunItem,
  RunStep,
  RunTodoItem,
  RunUsage,
  Subagent,
} from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { RunDetail } from './run-detail';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    get: vi.fn(),
    getPlan: vi.fn(),
    steps: vi.fn(),
    items: vi.fn(),
    todos: vi.fn(),
    subagents: vi.fn(),
    usage: vi.fn(),
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

const zeroUsage: RunUsage = {
  run_id: 'run-1',
  calls: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
  by_model: [],
};

interface DetailOptions {
  subagents?: Subagent[];
  usage?: RunUsage;
  onSelectRun?: (runId: string) => void;
  /** 覆盖 run 本身(子 run 头部、血缘文案)。 */
  run?: Partial<Run>;
}

function renderDetail(
  status: Run['status'],
  onClose = vi.fn(),
  steps: RunStep[] = [],
  todos: RunTodoItem[] = [],
  items: RunItem[] = [],
  options: DetailOptions = {},
) {
  vi.mocked(runsApi.get).mockResolvedValue({
    ...baseRun,
    status,
    ...options.run,
  });
  vi.mocked(runsApi.subagents).mockResolvedValue(options.subagents ?? []);
  vi.mocked(runsApi.usage).mockResolvedValue(options.usage ?? zeroUsage);
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
      <RunDetail
        runId="run-1"
        onClose={onClose}
        onSelectRun={options.onSelectRun}
      />
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

  /* ── P1-E:子代理面板与子 run 归属 ──────────────────────────────────── */

  const subagent: Subagent = {
    run_id: 'child-1',
    title: '整理依赖清单',
    agent_name: '依赖分析',
    status: 'completed',
    status_reason: null,
    parent_run_id: 'run-1',
    created_at: '2026-10-01T00:00:10Z',
    usage: {
      calls: 2,
      prompt_tokens: 20,
      completion_tokens: 10,
      total_tokens: 30,
    },
    summary: '梳理完成:依赖项 12 个。',
  };

  it('子代理面板显示 run 用量与子代理行,摘要可展开、可打开子 run', async () => {
    const onSelectRun = vi.fn();
    renderDetail('running', vi.fn(), [], [], [], {
      onSelectRun,
      subagents: [subagent],
      usage: {
        run_id: 'run-1',
        calls: 4,
        prompt_tokens: 300,
        completion_tokens: 100,
        total_tokens: 400,
        by_model: [
          {
            model: 'gpt-x',
            calls: 4,
            prompt_tokens: 300,
            completion_tokens: 100,
            total_tokens: 400,
          },
        ],
      },
    });

    const panel = await screen.findByTestId('run-subagents-panel');
    expect(within(panel).getByText('子代理')).toBeTruthy();
    expect(within(panel).getByText('1 个')).toBeTruthy();
    const usage = within(panel).getByTestId('run-usage');
    expect(usage.textContent).toContain('本 run 用量:4 次调用');
    expect(usage.textContent).toContain('合计 400 tokens');
    expect(within(panel).getByText('gpt-x')).toBeTruthy();
    expect(within(panel).getByTestId('run-subagent-total')).toHaveTextContent(
      '子代理合计:2 次调用 · 30 tokens',
    );

    const row = within(panel).getByTestId('run-subagent-child-1');
    expect(within(row).getByText('依赖分析 · 整理依赖清单')).toBeTruthy();
    expect(within(row).getByText('已完成')).toBeTruthy();
    expect(within(row).getByText('2 次调用 · 30 tokens')).toBeTruthy();
    // 摘要默认折叠,点开后渲染 Markdown 正文。
    expect(
      within(row).queryByTestId('run-subagent-summary-child-1'),
    ).toBeNull();
    fireEvent.click(
      within(row).getByTestId('run-subagent-summary-toggle-child-1'),
    );
    expect(
      within(row).getByTestId('run-subagent-summary-child-1'),
    ).toBeTruthy();
    expect(within(row).getByText('梳理完成:依赖项 12 个。')).toBeTruthy();

    fireEvent.click(within(row).getByTestId('run-subagent-open-child-1'));
    expect(onSelectRun).toHaveBeenCalledWith('child-1');
  });

  it('没有子代理也没有子代理过程项时不显示面板', async () => {
    renderDetail('completed');
    await waitFor(() => {
      expect(screen.getByText('修登录超时 bug')).toBeTruthy();
    });
    await waitFor(() => {
      expect(runsApi.subagents).toHaveBeenCalledWith('run-1');
    });
    expect(screen.queryByTestId('run-subagents-panel')).toBeNull();
  });

  it('只有子代理过程项时也显示面板并给出占位', async () => {
    renderDetail(
      'running',
      vi.fn(),
      [],
      [],
      [
        {
          item_id: 'run-1:1:subagent:1',
          thread_id: 'run-1',
          turn_id: 'turn-1',
          kind: 'subagent',
          phase: 'started',
          status: null,
          node: 'execute',
          step_id: null,
          attempt: 1,
          title: '子代理: 依赖分析',
          seq: 3,
          started_at: '2026-10-01T00:00:05Z',
          finished_at: null,
          duration_ms: null,
          data: {
            child_run_id: 'child-1',
            agent_name: '依赖分析',
            title: '子代理: 依赖分析',
          },
        },
      ],
    );

    expect(await screen.findByTestId('run-subagents-panel')).toBeTruthy();
    expect(screen.getByText('0 个')).toBeTruthy();
    expect(screen.getByText(/暂无子代理 run 记录/)).toBeTruthy();
  });

  it('子 run 详情显示父 run 归属并可切回父 run', async () => {
    const onSelectRun = vi.fn();
    renderDetail('awaiting_input', vi.fn(), [], [], [], {
      onSelectRun,
      run: {
        parent_run_id: 'run-9',
        parent_kind: 'subagent',
        agent_name: '依赖分析',
      },
    });

    const line = await screen.findByTestId('run-parent-subagent');
    expect(line.textContent).toContain('由父 run run-9…');
    expect(line.textContent).toContain('委派的子代理「依赖分析」');
    fireEvent.click(screen.getByTestId('run-parent-open'));
    expect(onSelectRun).toHaveBeenCalledWith('run-9');
  });

  it('重试派生的 run 仍显示重试来源文案', async () => {
    renderDetail('failed', vi.fn(), [], [], [], {
      run: { parent_run_id: 'run-9', parent_kind: 'retry' },
    });
    await waitFor(() => {
      expect(screen.getByText(/由 run run-9… 重试而来/)).toBeTruthy();
    });
    expect(screen.queryByTestId('run-parent-subagent')).toBeNull();
  });
});
