import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { evalApi } from '@/lib/eval-api';
import type {
  EvalBaselineList,
  EvalDataset,
  EvalDatasetList,
  EvalIndex,
} from '@/lib/eval-api';

import { EvalDatasetsPage } from './eval-datasets';
import { EvalGatesPage } from './eval-gates';

vi.mock('@/lib/eval-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/eval-api')>();
  return {
    ...actual,
    evalApi: {
      index: vi.fn(),
      datasets: vi.fn(),
      dataset: vi.fn(),
      baselines: vi.fn(),
    },
  };
});

const TOTALS = {
  cases: 2,
  passed: 2,
  failed: 0,
  pass_rate: 1,
  score: 1,
  tokens: 120,
  duration_ms: 30,
};

const DATASETS: EvalDatasetList = {
  items: [
    {
      name: 'agent-core',
      version: 1,
      description: 'Agent 主链路',
      owner: 'agent-platform',
      adapter: 'AgentOrchestratorAdapter',
      tags: ['offline', 'blocker'],
      case_count: 1,
      groups: ['conversation'],
      valid: true,
      error: null,
    },
    {
      name: 'broken',
      version: 0,
      description: '',
      owner: '',
      adapter: '',
      tags: [],
      case_count: 0,
      groups: [],
      valid: false,
      error: 'cases.jsonl 第 1 行不是合法 JSON',
    },
  ],
};

const DATASET: EvalDataset = {
  ...DATASETS.items[0],
  cases: [
    {
      id: 'conversation/multiturn',
      group: 'conversation',
      tags: ['multiturn'],
      input: { kind: 'chat', prompt: '你好' },
      graders: [
        {
          id: 'g1',
          type: 'contains',
          value: '你好',
          weight: 1,
          required: true,
        },
      ],
      notes: '两轮上下文',
    },
  ],
};

const INDEX: EvalIndex = {
  version: 1,
  generated_at: '2026-11-25T10:00:00Z',
  runs: [
    {
      id: 'run-1',
      created_at: '2026-11-25T10:00:00Z',
      trigger: 'ci',
      git_sha: 'abcdef1234',
      branch: 'main',
      pr: null,
      model: 'eval-scripted',
      environment: 'ci',
      report_version: 1,
      totals: TOTALS,
      drift_count: 0,
      suites: [
        {
          name: 'agent-core',
          dataset: 'agent-core',
          dataset_version: 1,
          totals: TOTALS,
          drift_count: 0,
        },
        {
          name: 'retrieval-quality',
          dataset: 'retrieval-quality',
          dataset_version: 1,
          totals: TOTALS,
          drift_count: 0,
        },
        {
          name: 'memory-quality',
          dataset: 'memory-quality',
          dataset_version: 1,
          totals: TOTALS,
          drift_count: 0,
        },
      ],
      failed_cases: [],
      dataset_versions: {
        'agent-core': 1,
        'retrieval-quality': 1,
        'memory-quality': 1,
      },
    },
  ],
};

const BASELINES: EvalBaselineList = {
  items: [
    {
      suite: 'agent-core',
      dataset_version: 1,
      evaluator_version: '1',
      git_sha: 'abcdef1234',
      approved_by: 'alice',
      approved_at: new Date().toISOString(),
      case_count: 2,
      passed: 2,
    },
    {
      suite: 'retrieval-quality',
      dataset_version: 2,
      evaluator_version: '1',
      git_sha: 'abcdef1234',
      approved_by: 'bob',
      approved_at: new Date().toISOString(),
      case_count: 2,
      passed: 2,
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
          <Route path="/evals/datasets" element={element} />
          <Route path="/evals/gates" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('EvalDatasetsPage', () => {
  it('展示数据集清单与选中数据集的用例定义', async () => {
    vi.mocked(evalApi.datasets).mockResolvedValue(DATASETS);
    vi.mocked(evalApi.dataset).mockResolvedValue(DATASET);
    renderAt('/evals/datasets?dataset=agent-core', <EvalDatasetsPage />);

    expect(await screen.findByText('数据集清单')).toBeInTheDocument();
    expect(screen.getAllByText('agent-core').length).toBeGreaterThan(0);
    expect(screen.getByText('校验失败')).toBeInTheDocument();
    expect(
      await screen.findByText('conversation/multiturn'),
    ).toBeInTheDocument();
    expect(screen.getByText('1 graders')).toBeInTheDocument();
    expect(screen.getByText(/"kind": "chat"/)).toBeInTheDocument();
    expect(screen.getByText(/"type": "contains"/)).toBeInTheDocument();
    expect(evalApi.dataset).toHaveBeenCalledWith('agent-core');
  });

  it('坏数据集详情给出服务端错误而不是空白', async () => {
    vi.mocked(evalApi.datasets).mockResolvedValue(DATASETS);
    vi.mocked(evalApi.dataset).mockRejectedValue(
      new Error('数据集 broken 校验失败'),
    );
    renderAt('/evals/datasets?dataset=broken', <EvalDatasetsPage />);

    expect(
      await screen.findByText(/数据集读取失败:数据集 broken 校验失败/),
    ).toBeInTheDocument();
  });
});

describe('EvalGatesPage', () => {
  it('按基线绑定状态标出版本不一致、缺基线与就绪', async () => {
    vi.mocked(evalApi.baselines).mockResolvedValue(BASELINES);
    vi.mocked(evalApi.index).mockResolvedValue(INDEX);
    renderAt('/evals/gates', <EvalGatesPage />);

    expect(await screen.findByText('基线门禁')).toBeInTheDocument();
    expect(screen.getByText('版本不一致')).toBeInTheDocument();
    expect(screen.getByText('缺基线')).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getAllByText('就绪').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/v2 · 2 条/)).toBeInTheDocument();
  });
});