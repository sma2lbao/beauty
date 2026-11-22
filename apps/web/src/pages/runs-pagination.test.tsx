import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run, RunPage } from '@/lib/runs-api';
import { runsApi, streamRunEvents } from '@/lib/runs-api';
import { resetRunBoard } from '@/stores/run-board-store';

import { RunsPage } from './runs';

function page(items: Run[], next_cursor: string | null = null): RunPage {
  return { items, next_cursor };
}

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      list: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      getPlan: vi.fn(),
      steps: vi.fn(async () => []),
      todos: vi.fn(async () => ({
        todos: [],
        counts: { pending: 0, in_progress: 0, completed: 0 },
        revision: 0,
      })),
      events: vi.fn(),
      subagents: vi.fn(async () => []),
      usage: vi.fn(async () => ({
        run_id: 'run-1',
        calls: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        by_model: [],
      })),
      generatePlan: vi.fn(),
      updatePlan: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      confirm: vi.fn(),
      resume: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
      artifact: vi.fn(),
      artifacts: vi.fn(),
      downloadArtifact: vi.fn(),
      instructions: vi.fn(),
      queueInstruction: vi.fn(),
    },
    streamRunEvents: vi.fn(),
  };
});

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周邮件成周报',
    goal: '汇总本周邮件,生成周报草稿',
    status: 'pending',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: ['覆盖 5 个工作日'],
    status_reason: null,
    parent_run_id: null,
    conversation_id: null,
    created_at: '2026-10-12T10:00:00+00:00',
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <RunsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('RunsPage 搜索与分页', () => {
  beforeEach(() => {
    resetRunBoard();
    vi.mocked(streamRunEvents).mockImplementation(
      () => new Promise<void>(() => {}),
    );
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'pending',
      plan_markdown: '',
    });
    vi.mocked(runsApi.instructions).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('搜索按关键词重新请求,无命中时给搜索空态', async () => {
    vi.mocked(runsApi.list).mockImplementation(async (params = {}) =>
      params.q
        ? page([makeRun({ id: 'run-2', title: '修复登录问题' })])
        : page([makeRun({ id: 'run-1', title: '整理本周邮件成周报' })]),
    );
    renderPage();

    expect(await screen.findByText('整理本周邮件成周报')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('搜索任务'), {
      target: { value: '登录' },
    });

    expect(await screen.findByText('修复登录问题')).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(runsApi.list)).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: '登录' }),
      ),
    );

    vi.mocked(runsApi.list).mockResolvedValue(page([]));
    fireEvent.change(screen.getByLabelText('搜索任务'), {
      target: { value: '不存在的任务' },
    });

    expect(await screen.findByText('没有匹配的任务。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清空搜索' }));
    expect(await screen.findByText('整理本周邮件成周报')).toBeInTheDocument();
  });

  it('有下一页时点「加载更多」用游标追加', async () => {
    vi.mocked(runsApi.list)
      .mockResolvedValueOnce(
        page([makeRun({ id: 'run-1', title: '第一页任务' })], 'c2'),
      )
      .mockResolvedValueOnce(
        page([makeRun({ id: 'run-2', title: '第二页任务' })]),
      );
    renderPage();

    expect(await screen.findByText('第一页任务')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));

    expect(await screen.findByText('第二页任务')).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(runsApi.list)).toHaveBeenLastCalledWith(
        expect.objectContaining({ cursor: 'c2' }),
      ),
    );
    expect(screen.getByText('第一页任务')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '加载更多' }),
    ).not.toBeInTheDocument();
  });
});
