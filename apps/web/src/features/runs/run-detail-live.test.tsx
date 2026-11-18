/**
 * 详情抽屉的读模型来源(事件分类学 §11/§13):
 * - 连上 SSE:步骤/过程项/清单由事件投影,一个读模型端点都不调;
 * - 断线:必须回落端点,否则界面是死的;
 * - 状态/用量事件到达时只失效对应查询,不整页重拉。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run, RunEvent, RunStep, RunTodoItem } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { RunDetail } from './run-detail';
import { readModelEvents } from './run-detail-test-events';

const stream = vi.hoisted(() => ({
  events: [] as RunEvent[],
  connected: true,
  ended: false,
}));

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
    sideEffects: vi.fn(),
    rewind: vi.fn(),
    fork: vi.fn(),
    artifacts: vi.fn(),
  },
}));

vi.mock('@/features/runs/use-run-events', () => ({
  useRunEvents: () => ({
    events: stream.events,
    connected: stream.connected,
    ended: stream.ended,
  }),
}));

const baseRun: Run = {
  id: 'run-1',
  title: '修登录超时 bug',
  goal: '修复登录 30s 超时',
  status: 'running',
  rounds: 1,
  attempts: 1,
  acceptance_criteria: ['登录不再超时'],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

const step: RunStep = {
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
};

const todos: RunTodoItem[] = [
  { content: '读取配置', status: 'completed' },
  { content: '实现工具', status: 'in_progress' },
];

function mockApi() {
  vi.mocked(runsApi.get).mockResolvedValue(baseRun);
  vi.mocked(runsApi.getPlan).mockResolvedValue({
    run_id: 'run-1',
    status: 'running',
    plan_markdown: '',
  });
  vi.mocked(runsApi.instructions).mockResolvedValue([]);
  vi.mocked(runsApi.subagents).mockResolvedValue([]);
  vi.mocked(runsApi.usage).mockResolvedValue({
    run_id: 'run-1',
    calls: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    by_model: [],
  });
  vi.mocked(runsApi.scores).mockResolvedValue([]);
  vi.mocked(runsApi.steps).mockResolvedValue([step]);
  vi.mocked(runsApi.items).mockResolvedValue([]);
  vi.mocked(runsApi.todos).mockResolvedValue({
    todos,
    counts: { pending: 0, in_progress: 1, completed: 1 },
    revision: 1,
  });
}

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (
    <QueryClientProvider client={client}>
      <RunDetail runId="run-1" onClose={vi.fn()} />
    </QueryClientProvider>
  );
  const view = render(tree);
  return {
    rerender: () =>
      view.rerender(
        <QueryClientProvider client={client}>
          <RunDetail runId="run-1" onClose={vi.fn()} />
        </QueryClientProvider>,
      ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  stream.events = [];
  stream.connected = true;
  stream.ended = false;
  mockApi();
});

describe('RunDetail 读模型来源', () => {
  it('连上 SSE 时步骤与清单来自事件,不调读模型端点', async () => {
    stream.events = readModelEvents({ steps: [step], todos });

    renderDetail();

    expect(await screen.findByTestId('run-step-timeline')).toBeTruthy();
    expect(screen.getByText('1/2')).toBeTruthy();
    expect(runsApi.steps).not.toHaveBeenCalled();
    expect(runsApi.items).not.toHaveBeenCalled();
    expect(runsApi.todos).not.toHaveBeenCalled();
  });

  it('断线时回落读模型端点', async () => {
    stream.connected = false;

    renderDetail();

    await waitFor(() => {
      expect(screen.getByText('1/2')).toBeTruthy();
    });
    expect(runsApi.steps).toHaveBeenCalledWith('run-1');
    expect(runsApi.todos).toHaveBeenCalledWith('run-1');
    expect(runsApi.items).toHaveBeenCalledWith('run-1');
  });

  it('状态事件到达时重取 run 与计划,不整页重拉', async () => {
    const { rerender } = renderDetail();
    await waitFor(() => expect(runsApi.get).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(runsApi.getPlan).toHaveBeenCalledTimes(1));

    await act(async () => {
      stream.events = [
        { seq: 9, type: 'run.planned.v1', payload: { attempt: 1 } },
      ];
      rerender();
    });

    await waitFor(() => expect(runsApi.get).toHaveBeenCalledTimes(2));
    expect(runsApi.getPlan).toHaveBeenCalledTimes(2);
    expect(runsApi.usage).toHaveBeenCalledTimes(1);
  });

  it('用量事件只失效用量,不重取 run', async () => {
    const { rerender } = renderDetail();
    await waitFor(() => expect(runsApi.usage).toHaveBeenCalledTimes(1));

    await act(async () => {
      stream.events = [{ seq: 11, type: 'run.usage.v1', payload: {} }];
      rerender();
    });

    await waitFor(() => expect(runsApi.usage).toHaveBeenCalledTimes(2));
    expect(runsApi.get).toHaveBeenCalledTimes(1);
  });
});
