/**
 * P1-C:运行详情「检查点」页签。
 *
 * 覆盖:列表元信息与摘要、非 runnable 行不可回退、回退/分叉二次确认与载荷、
 * 409 run.checkpoint_not_found 中文提示、概览里的超时徽标与配置。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import type {
  Plan,
  Run,
  RunCheckpoint,
  RunItem,
  RunStep,
  RunTodoItem,
  RunUsage,
  RunScore,
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
    scores: vi.fn(),
    createScore: vi.fn(),
    instructions: vi.fn(),
    retry: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    generatePlan: vi.fn(),
    updatePlan: vi.fn(),
    queueInstruction: vi.fn(),
    resume: vi.fn(),
    cancel: vi.fn(),
    checkpoints: vi.fn(),
    rewind: vi.fn(),
    fork: vi.fn(),
    artifacts: vi.fn(),
  },
}));

vi.mock('@/features/runs/use-run-events', () => ({
  useRunEvents: () => ({ events: [], connected: true }),
}));

const baseRun: Run = {
  id: 'run-1',
  title: '修登录超时 bug',
  goal: '修复登录 30s 超时',
  status: 'completed',
  rounds: 2,
  attempts: 1,
  acceptance_criteria: ['登录不再超时'],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  timeout_seconds: 120,
  priority: 5,
  created_at: '2026-10-01T00:00:00Z',
};

const checkpoints: RunCheckpoint[] = [
  {
    checkpoint_id: 'ck-1',
    step: 3,
    source: 'planner',
    next: ['executor', 'reporter'],
    created_at: '2026-10-01T00:10:00Z',
    parent_checkpoint_id: null,
    runnable: true,
    summary: {
      rounds: 2,
      plan_chars: 120,
      tool_calls: 4,
      todos_count: 2,
      artifact_ready: true,
    },
  },
  {
    checkpoint_id: 'ck-2',
    step: 5,
    source: 'executor',
    next: [],
    created_at: '2026-10-01T00:20:00Z',
    parent_checkpoint_id: 'ck-1',
    runnable: false,
    summary: {
      rounds: 4,
      plan_chars: 120,
      tool_calls: 9,
      todos_count: 2,
      artifact_ready: false,
    },
  },
];

const plan: Plan = {
  run_id: 'run-1',
  status: 'completed',
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

const forkedRun: Run = { ...baseRun, id: 'run-2', status: 'pending' };

interface DetailOptions {
  run?: Partial<Run>;
  onSelectRun?: (runId: string) => void;
  checkpoints?: RunCheckpoint[];
}

function renderDetail(options: DetailOptions = {}) {
  vi.mocked(runsApi.get).mockResolvedValue({ ...baseRun, ...options.run });
  vi.mocked(runsApi.getPlan).mockResolvedValue(plan);
  vi.mocked(runsApi.steps).mockResolvedValue([] as RunStep[]);
  vi.mocked(runsApi.items).mockResolvedValue([] as RunItem[]);
  vi.mocked(runsApi.todos).mockResolvedValue({
    todos: [] as RunTodoItem[],
    counts: { pending: 0, in_progress: 0, completed: 0 },
    revision: 0,
  });
  vi.mocked(runsApi.subagents).mockResolvedValue([] as Subagent[]);
  vi.mocked(runsApi.usage).mockResolvedValue(zeroUsage);
  vi.mocked(runsApi.scores).mockResolvedValue([] as RunScore[]);
  vi.mocked(runsApi.instructions).mockResolvedValue([]);
  vi.mocked(runsApi.artifacts).mockResolvedValue([]);
  vi.mocked(runsApi.checkpoints).mockResolvedValue(
    options.checkpoints ?? checkpoints,
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <RunDetail
        runId="run-1"
        onClose={vi.fn()}
        onSelectRun={options.onSelectRun}
      />
    </QueryClientProvider>,
  );
}

async function openCheckpointsTab() {
  fireEvent.click(await screen.findByRole('tab', { name: '检查点' }));
}

describe('RunDetail 检查点页签', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('展示 step/来源/next/摘要,runnable 才可回退', async () => {
    renderDetail();
    await openCheckpointsTab();

    const runnableRow = await screen.findByTestId('run-checkpoint-ck-1');
    expect(runnableRow).toHaveTextContent('#3');
    expect(runnableRow).toHaveTextContent('source planner');
    expect(runnableRow).toHaveTextContent('executor → reporter');
    expect(runnableRow).toHaveTextContent('轮次 2');
    expect(runnableRow).toHaveTextContent('可回退');
    expect(
      within(runnableRow).getByRole('button', { name: /回退到此/ }),
    ).toBeEnabled();

    const doneRow = screen.getByTestId('run-checkpoint-ck-2');
    expect(doneRow).toHaveTextContent('已推进');
    expect(
      within(doneRow).getByRole('button', { name: /回退到此/ }),
    ).toBeDisabled();
  });

  it('回退需二次确认,确认后带原因调用 rewind', async () => {
    renderDetail();
    await openCheckpointsTab();
    const row = await screen.findByTestId('run-checkpoint-ck-1');

    fireEvent.click(within(row).getByRole('button', { name: /回退到此/ }));
    expect(await screen.findByText('回退到此检查点?')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('回退原因(可选)'), {
      target: { value: '计划错了' },
    });
    fireEvent.click(screen.getByRole('button', { name: '确认回退' }));

    await waitFor(() =>
      expect(runsApi.rewind).toHaveBeenCalledWith('run-1', {
        checkpoint_id: 'ck-1',
        reason: '计划错了',
      }),
    );
  });

  it('分叉需二次确认,成功后切到新 run', async () => {
    const onSelectRun = vi.fn();
    vi.mocked(runsApi.fork).mockResolvedValue(forkedRun);
    renderDetail({ onSelectRun });
    await openCheckpointsTab();
    const row = await screen.findByTestId('run-checkpoint-ck-1');

    fireEvent.click(within(row).getByRole('button', { name: '分叉' }));
    expect(await screen.findByText('从检查点分叉')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('新 run 标题(可选)'), {
      target: { value: '新的分支' },
    });
    fireEvent.click(screen.getByRole('button', { name: '确认分叉' }));

    await waitFor(() =>
      expect(runsApi.fork).toHaveBeenCalledWith('run-1', {
        checkpoint_id: 'ck-1',
        title: '新的分支',
      }),
    );
    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith('run-2'));
  });

  it('409 checkpoint 失效就地中文提示', async () => {
    vi.mocked(runsApi.rewind).mockRejectedValue(
      new ApiError(
        409,
        'run.checkpoint_not_found',
        'checkpoint 不存在或不属于当前 run',
      ),
    );
    renderDetail();
    await openCheckpointsTab();
    const row = await screen.findByTestId('run-checkpoint-ck-1');

    fireEvent.click(within(row).getByRole('button', { name: /回退到此/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确认回退' }));

    expect(
      await screen.findByText(
        /检查点不存在或不属于当前 run\(可能已被清理\),请刷新列表后重试。/,
      ),
    ).toBeInTheDocument();
  });

  it('概览展示超时配置与超时失败徽标/原因', async () => {
    renderDetail({
      run: {
        status: 'failed',
        status_reason: '执行超时（120 秒），可重试',
        rounds: 3,
      },
    });

    expect(await screen.findByTestId('run-timeout-badge')).toHaveTextContent(
      '超时',
    );
    expect(screen.getByTestId('run-timeout')).toHaveTextContent('超时 120 秒');
    expect(screen.getByTestId('run-priority')).toHaveTextContent('优先级 +5');
    expect(screen.getByText(/执行超时（120 秒），可重试/)).toBeInTheDocument();
  });
});
