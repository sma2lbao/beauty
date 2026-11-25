import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { evalApi } from '@/lib/eval-api';
import type {
  EvalCompare,
  EvalIndex,
  EvalRunMeta,
  EvalTotals,
} from '@/lib/eval-api';

import { EvalComparePage } from './eval-compare';

vi.mock('@/lib/eval-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/eval-api')>();
  return {
    ...actual,
    evalApi: {
      index: vi.fn(),
      run: vi.fn(),
      compare: vi.fn(),
    },
  };
});

const BASE_TOTALS: EvalTotals = {
  cases: 2,
  passed: 1,
  failed: 1,
  pass_rate: 0.5,
  score: 0.75,
  tokens: 100,
  duration_ms: 20,
};

const CAND_TOTALS: EvalTotals = {
  cases: 3,
  passed: 2,
  failed: 1,
  pass_rate: 0.6667,
  score: 0.8,
  tokens: 110,
  duration_ms: 15,
};

function run(id: string, totals: EvalTotals, environment: string): EvalRunMeta {
  return {
    id,
    created_at: '2026-11-25T10:00:00Z',
    trigger: 'local',
    git_sha: id + '-sha',
    branch: 'main',
    pr: null,
    model: 'eval-scripted',
    environment,
    report_version: 1,
    totals,
    drift_count: 0,
    suites: [],
    failed_cases: [],
    dataset_versions: { 'agent-core': 1 },
  };
}

const INDEX: EvalIndex = {
  version: 1,
  generated_at: '2026-11-25T10:00:00Z',
  runs: [run('cand', CAND_TOTALS, 'local'), run('base', BASE_TOTALS, 'ci')],
};

const COMPARE: EvalCompare = {
  base: INDEX.runs[1],
  candidate: INDEX.runs[0],
  totals: {
    regressed: 1,
    fixed: 1,
    added: 1,
    removed: 1,
    unchanged: 0,
    base_pass_rate: 0.5,
    candidate_pass_rate: 0.6667,
    pass_rate_delta: 0.1667,
    tokens_delta: 10,
    duration_delta_ms: -5,
  },
  suites: [
    {
      name: 'agent-core',
      base_totals: BASE_TOTALS,
      candidate_totals: CAND_TOTALS,
      regressed: 1,
      fixed: 1,
      added: 1,
      removed: 1,
      unchanged: 0,
    },
  ],
  cases: [
    {
      suite: 'agent-core',
      case_id: 'chat/a',
      status: 'regressed',
      base_ok: true,
      candidate_ok: false,
      base_score: 1,
      candidate_score: 0,
      score_delta: -1,
      tokens_delta: 10,
      duration_delta_ms: 1,
      base_error: null,
      candidate_error: null,
      failures: ['回复缺少关键词:周报'],
      tags: ['blocker'],
    },
    {
      suite: 'agent-core',
      case_id: 'chat/b',
      status: 'fixed',
      base_ok: false,
      candidate_ok: true,
      base_score: 0,
      candidate_score: 1,
      score_delta: 1,
      tokens_delta: -2,
      duration_delta_ms: -1,
      base_error: null,
      candidate_error: null,
      failures: [],
      tags: [],
    },
    {
      suite: 'agent-core',
      case_id: 'chat/d',
      status: 'added',
      base_ok: null,
      candidate_ok: true,
      base_score: null,
      candidate_score: 1,
      score_delta: 0,
      tokens_delta: 5,
      duration_delta_ms: 2,
      base_error: null,
      candidate_error: null,
      failures: [],
      tags: ['new'],
    },
    {
      suite: 'agent-core',
      case_id: 'chat/c',
      status: 'removed',
      base_ok: true,
      candidate_ok: null,
      base_score: 1,
      candidate_score: null,
      score_delta: 0,
      tokens_delta: -5,
      duration_delta_ms: -1,
      base_error: null,
      candidate_error: null,
      failures: [],
      tags: [],
    },
  ],
};

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
          <Route path="/evals/compare" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('EvalComparePage', () => {
  it('默认用最新两次运行,按 URL 过滤回归并保留深链', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    vi.mocked(evalApi.compare).mockResolvedValue(COMPARE);
    renderAt('/evals/compare?status=regressed', <EvalComparePage />);

    expect(await screen.findByText('套件差异')).toBeInTheDocument();
    expect(evalApi.compare).toHaveBeenCalledWith('base', 'cand');
    expect(screen.getByText('基线通过 → 候选失败')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'chat/a' })).toHaveAttribute(
      'href',
      '/evals/runs/cand?suite=agent-core&case=chat%2Fa&tab=result',
    );
    expect(
      screen.queryByRole('link', { name: 'chat/b' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('1/4')).toBeInTheDocument();
  });

  it('切到全部后展示修复、新增与只在基线存在的用例', async () => {
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    vi.mocked(evalApi.compare).mockResolvedValue(COMPARE);
    renderAt('/evals/compare', <EvalComparePage />);

    expect(
      await screen.findByRole('link', { name: 'chat/b' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全部' }));

    expect(await screen.findByRole('link', { name: 'chat/c' })).toHaveAttribute(
      'href',
      '/evals/runs/base?suite=agent-core&case=chat%2Fc&tab=result',
    );
    expect(screen.getByRole('link', { name: 'chat/d' })).toHaveAttribute(
      'href',
      '/evals/runs/cand?suite=agent-core&case=chat%2Fd&tab=result',
    );
  });
});
