import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { evalApi, findCase, flattenCases } from '@/lib/eval-api';
import type { EvalIndex, EvalReport, EvalRunSummary } from '@/lib/eval-api';

import { EvalRunPage } from './eval-run';
import { EvalsPage } from './evals';

vi.mock('@/lib/eval-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/eval-api')>();
  return {
    ...actual,
    evalApi: {
      index: vi.fn(),
      run: vi.fn(),
      summary: vi.fn(),
      cases: vi.fn(),
      case: vi.fn(),
    },
  };
});

const TOTALS = {
  cases: 2,
  passed: 1,
  failed: 1,
  pass_rate: 0.5,
  score: 0.75,
  tokens: 321,
  duration_ms: 1234,
};

const INDEX: EvalIndex = {
  version: 1,
  generated_at: '2026-11-24T15:46:46Z',
  runs: [
    {
      id: 'run-new',
      created_at: '2026-11-24T15:46:46Z',
      trigger: 'local',
      git_sha: '3f42641abc',
      branch: 'main',
      pr: null,
      model: 'eval-scripted',
      environment: 'local',
      report_version: 1,
      totals: TOTALS,
      drift_count: 1,
      suites: [
        {
          name: 'agent-core',
          dataset: 'agent-core',
          dataset_version: 1,
          totals: TOTALS,
          drift_count: 1,
        },
      ],
      failed_cases: [
        {
          suite: 'agent-core',
          case_id: 'chat/fail',
          failures: ['回复缺少关键词:周报'],
        },
      ],
      dataset_versions: { 'agent-core': 1 },
    },
    {
      id: 'run-old',
      created_at: '2026-11-24T14:42:00Z',
      trigger: 'ci',
      git_sha: 'ad6d858def',
      branch: 'main',
      pr: null,
      model: 'eval-scripted',
      environment: 'ci',
      report_version: 1,
      totals: { ...TOTALS, passed: 2, failed: 0, pass_rate: 1, score: 1 },
      drift_count: 0,
      suites: [
        {
          name: 'agent-core',
          dataset: 'agent-core',
          dataset_version: 1,
          totals: { ...TOTALS, passed: 2, failed: 0, pass_rate: 1, score: 1 },
          drift_count: 0,
        },
      ],
      failed_cases: [],
      dataset_versions: { 'agent-core': 1 },
    },
  ],
};

const REPORT: EvalReport = {
  version: 1,
  generated_at: '2026-11-24T15:46:46Z',
  totals: TOTALS,
  suites: [
    {
      name: 'agent-core',
      dataset: 'agent-core',
      dataset_version: 1,
      generated_at: '2026-11-24T15:46:46Z',
      totals: TOTALS,
      cases: [
        {
          id: 'chat/pass',
          group: 'chat',
          tags: ['smoke', 'conversation'],
          ok: true,
          score: 1,
          duration_ms: 12,
          tokens: 100,
          output: '这是通过用例的回复',
          error: null,
          fields: { prompt_0: '你好', reply_0: '你好,有什么可以帮你?' },
          events: ['run.created.v1'],
          tool_calls: [],
          metrics: { total_tokens: 100 },
          graders: [
            {
              id: 'g1',
              type: 'contains',
              ok: true,
              required: true,
              weight: 1,
              message: '',
            },
          ],
          failures: [],
        },
        {
          id: 'chat/fail',
          group: 'chat',
          tags: ['blocker'],
          ok: false,
          score: 0.5,
          duration_ms: 34,
          tokens: 221,
          output: '已发布',
          error: null,
          fields: {
            prompt_0: '整理周报',
            tool_call_details: [
              {
                name: 'external_publish',
                arguments: { title: '周报' },
                ok: true,
                risk: 'danger',
                decision: 'approved',
                error_code: null,
                duration_ms: 0,
              },
            ],
          },
          events: ['run.created.v1', 'run.tool.called.v1'],
          tool_calls: ['external_publish'],
          metrics: { total_tokens: 221 },
          graders: [
            {
              id: 'g1',
              type: 'contains',
              ok: false,
              required: true,
              weight: 1,
              message: '回复缺少关键词:周报',
            },
          ],
          failures: ['回复缺少关键词:周报'],
        },
      ],
    },
  ],
};

const SUMMARY: EvalRunSummary = {
  id: 'run-new',
  generated_at: '2026-11-24T15:46:46Z',
  totals: TOTALS,
  suites: INDEX.runs[0].suites,
};

function mockRun() {
  vi.mocked(evalApi.summary).mockResolvedValue(SUMMARY);
  vi.mocked(evalApi.cases).mockImplementation(async (_runId, suite, options = {}) => {
    const all = flattenCases(REPORT).filter((item) => item.suite === suite);
    const filtered = all.filter((item) => {
      if (options.status === 'passed') return item.ok;
      if (options.status === 'failed') return !item.ok;
      return true;
    });
    const offset = options.offset ?? 0;
    const limit = options.limit ?? 50;
    return {
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      limit,
      offset,
    };
  });
  vi.mocked(evalApi.case).mockImplementation(async (_runId, _suite, caseId) => {
    const found = findCase(REPORT, caseId);
    if (!found) throw new Error('missing case');
    return found;
  });
}

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function renderAt(path: string, element: ReactNode) {
  return render(
    <QueryClientProvider client={client()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/evals" element={element} />
          <Route path="/evals/runs/:runId" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('EvalsPage', () => {
  it('展示 KPI、套件健康度、失败用例和运行历史', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    renderAt('/evals', <EvalsPage />);

    expect(await screen.findByText('套件健康度')).toBeInTheDocument();
    expect(screen.getByText('需要关注')).toBeInTheDocument();
    expect(screen.getByText('1 个失败')).toBeInTheDocument();
    expect(screen.getByText('最近运行')).toBeInTheDocument();
    expect(screen.getByText('run-old')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /chat\/fail/ })).toHaveAttribute(
      'href',
      '/evals/runs/run-new?suite=agent-core&case=chat%2Ffail&tab=result',
    );
    expect(screen.getAllByText('50%').length).toBeGreaterThan(0);
  });

  it('没有运行时给出空态而不是崩溃', async () => {
    vi.mocked(evalApi.index).mockResolvedValue({
      version: 1,
      generated_at: '2026-11-24T15:46:46Z',
      runs: [],
    });
    renderAt('/evals', <EvalsPage />);

    expect(await screen.findByText('还没有评测快照')).toBeInTheDocument();
  });
});

describe('EvalRunPage', () => {
  it('按 URL 过滤失败用例并在抽屉里展示评分器与工具轨迹', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    mockRun();
    renderAt(
      '/evals/runs/run-new?suite=agent-core&status=failed',
      <EvalRunPage />,
    );

    expect(await screen.findByText('chat/fail')).toBeInTheDocument();
    expect(screen.queryByText('chat/pass')).not.toBeInTheDocument();
    expect(screen.getByText('回复缺少关键词:周报')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '全部' }));
    expect(await screen.findByText('chat/pass')).toBeInTheDocument();

    fireEvent.click(screen.getByText('chat/fail'));
    expect(await screen.findByLabelText('评测用例详情')).toBeInTheDocument();
    expect(screen.getAllByText('评分器').length).toBeGreaterThan(0);
    expect(screen.getByText('weight 1 · required')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '轨迹' }));
    expect(await screen.findByText('external_publish')).toBeInTheDocument();
    expect(screen.getByText('run.tool.called.v1')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByLabelText('评测用例详情')).not.toBeInTheDocument();
  });

  it('用例超过一页时走服务端分页', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    vi.mocked(evalApi.summary).mockResolvedValue(SUMMARY);
    const base = REPORT.suites[0].cases[0];
    const all = Array.from({ length: 120 }, (_, index) => ({
      ...base,
      id: `chat/case-${index}`,
      suite: 'agent-core',
    }));
    vi.mocked(evalApi.cases).mockImplementation(
      async (_runId, _suite, options = {}) => {
        const offset = options.offset ?? 0;
        const limit = options.limit ?? 50;
        return {
          items: all.slice(offset, offset + limit),
          total: all.length,
          limit,
          offset,
        };
      },
    );
    vi.mocked(evalApi.case).mockResolvedValue(all[0]);
    renderAt('/evals/runs/run-new?suite=agent-core', <EvalRunPage />);

    expect(
      await screen.findByText('第 1 / 3 页 · 每页 50 条'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(
      await screen.findByText('第 2 / 3 页 · 每页 50 条'),
    ).toBeInTheDocument();
    expect(await screen.findByText('chat/case-50')).toBeInTheDocument();
  });

  it('从 URL 深链打开用例,并切到输入输出页签', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    mockRun();
    renderAt(
      '/evals/runs/run-new?suite=agent-core&case=chat%2Fpass&tab=io',
      <EvalRunPage />,
    );

    expect(await screen.findByLabelText('评测用例详情')).toBeInTheDocument();
    expect(screen.getByText('prompt_0')).toBeInTheDocument();
    expect(screen.getByText('这是通过用例的回复')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('关闭用例详情'));
    expect(screen.queryByLabelText('评测用例详情')).not.toBeInTheDocument();
  });
});
