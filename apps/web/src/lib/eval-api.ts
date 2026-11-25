/**
 * 评测平台只读数据层。
 *
 * 优先读 eval-service(同源 `/api/eval` → 网关 → :8009);服务未启动或
 * 未部署时回退到 `public/eval` 静态快照,由
 * `node scripts/eval/publish-web.mjs` 从 evalkit 的 JSON 报告发布。
 * 两条路径返回同一份报告结构,页面与类型不变。
 */
import { createHttpClient } from '@/lib/http';

export type EvalTotals = {
  cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  score: number;
  tokens: number;
  duration_ms: number;
};

export type EvalGrader = {
  id: string;
  type: string;
  ok: boolean;
  required: boolean;
  weight: number;
  message: string;
};

export type EvalCase = {
  id: string;
  group: string;
  tags: string[];
  ok: boolean;
  score: number;
  duration_ms: number;
  tokens: number;
  output: string;
  error: string | null;
  fields: Record<string, unknown>;
  events: string[];
  tool_calls: string[];
  metrics: Record<string, unknown>;
  graders: EvalGrader[];
  failures: string[];
};

/** 运行详情里的用例:带 suite 便于跨套件定位。 */
export type EvalCaseWithSuite = EvalCase & { suite: string };

export type EvalSuite = {
  name: string;
  dataset: string | null;
  dataset_version: number | null;
  generated_at: string;
  totals: EvalTotals;
  cases: EvalCase[];
};

export type EvalReport = {
  version: number;
  generated_at: string;
  totals: EvalTotals;
  suites: EvalSuite[];
};

/** 运行摘要:不含逐用例明细，供详情页首屏与分页查询使用。 */
export type EvalRunSummary = {
  id: string;
  generated_at: string;
  totals: EvalTotals;
  suites: EvalSuiteSummary[];
};

/** 服务端分页后的用例页。 */
export type EvalCasePage = {
  items: EvalCaseWithSuite[];
  total: number;
  limit: number;
  offset: number;
};

export type EvalSuiteSummary = {
  name: string;
  dataset: string | null;
  dataset_version: number | null;
  totals: EvalTotals;
  drift_count: number;
};

export type EvalFailedCase = {
  suite: string;
  case_id: string;
  failures: string[];
};

export type EvalRunMeta = {
  id: string;
  created_at: string;
  trigger: string;
  git_sha: string;
  branch: string;
  pr: number | null;
  model: string;
  environment: string;
  report_version: number;
  totals: EvalTotals;
  drift_count: number;
  suites: EvalSuiteSummary[];
  failed_cases: EvalFailedCase[];
  dataset_versions: Record<string, number | null>;
};

export type EvalIndex = {
  version: number;
  generated_at: string;
  runs: EvalRunMeta[];
};

export type EvalDatasetCase = {
  id: string;
  group: string;
  tags: string[];
  input: Record<string, unknown>;
  graders: Record<string, unknown>[];
  notes: string;
};

/** 数据集清单项;坏数据集也保留在清单里,valid=false + error 说明原因。 */
export type EvalDatasetSummary = {
  name: string;
  version: number;
  description: string;
  owner: string;
  adapter: string;
  tags: string[];
  case_count: number;
  groups: string[];
  valid: boolean;
  error: string | null;
};

export type EvalDataset = EvalDatasetSummary & {
  cases: EvalDatasetCase[];
};

export type EvalDatasetList = {
  items: EvalDatasetSummary[];
};

/** 已批准基线;case_count/passed 由服务端按 payload 现算。 */
export type EvalBaseline = {
  suite: string;
  dataset_version: number | null;
  evaluator_version: string;
  git_sha: string;
  approved_by: string;
  approved_at: string;
  case_count: number;
  passed: number;
};

export type EvalBaselineList = {
  items: EvalBaseline[];
};

export type EvalCompareStatus =
  'regressed' | 'fixed' | 'added' | 'removed' | 'unchanged';

/** 两次运行的用例级差异汇总;差值均为 candidate - base。 */
export type EvalCompareTotals = {
  regressed: number;
  fixed: number;
  added: number;
  removed: number;
  unchanged: number;
  base_pass_rate: number;
  candidate_pass_rate: number;
  pass_rate_delta: number;
  tokens_delta: number;
  duration_delta_ms: number;
};

export type EvalCompareSuite = {
  name: string;
  base_totals: EvalTotals | null;
  candidate_totals: EvalTotals | null;
  regressed: number;
  fixed: number;
  added: number;
  removed: number;
  unchanged: number;
};

export type EvalCompareCase = {
  suite: string;
  case_id: string;
  status: EvalCompareStatus;
  base_ok: boolean | null;
  candidate_ok: boolean | null;
  base_score: number | null;
  candidate_score: number | null;
  score_delta: number;
  tokens_delta: number;
  duration_delta_ms: number;
  base_error: string | null;
  candidate_error: string | null;
  failures: string[];
  tags: string[];
};

export type EvalCompare = {
  base: EvalRunMeta;
  candidate: EvalRunMeta;
  totals: EvalCompareTotals;
  suites: EvalCompareSuite[];
  cases: EvalCompareCase[];
};

const http = createHttpClient('');

/** 静态快照只取一次:分页/单用例查询回退时共用同一份报告。 */
const staticReports = new Map<string, Promise<EvalReport>>();

function loadStaticReport(runId: string): Promise<EvalReport> {
  const cached = staticReports.get(runId);
  if (cached) return cached;
  const pending = http.request<EvalReport>(
    '/eval/runs/' + encodeURIComponent(runId) + '.json',
  );
  staticReports.set(runId, pending);
  pending.catch(() => staticReports.delete(runId));
  return pending;
}

async function loadReport(runId: string): Promise<EvalReport> {
  try {
    return await http.request<EvalReport>(
      '/api/eval/runs/' + encodeURIComponent(runId),
    );
  } catch {
    return loadStaticReport(runId);
  }
}

function summaryOf(runId: string, report: EvalReport): EvalRunSummary {
  return {
    id: runId,
    generated_at: report.generated_at,
    totals: report.totals,
    suites: report.suites.map((suite) => ({
      name: suite.name,
      dataset: suite.dataset,
      dataset_version: suite.dataset_version,
      totals: suite.totals,
      drift_count: 0,
    })),
  };
}

export const evalApi = {
  index: async (): Promise<EvalIndex> => {
    try {
      return await http.request<EvalIndex>('/api/eval/runs?limit=5');
    } catch {
      return http.request<EvalIndex>('/eval/index.json');
    }
  },
  run: loadReport,
  summary: async (runId: string): Promise<EvalRunSummary> => {
    try {
      const meta = await http.request<EvalRunMeta>(
        '/api/eval/runs/' + encodeURIComponent(runId) + '/summary',
      );
      return {
        id: meta.id,
        generated_at: meta.created_at,
        totals: meta.totals,
        suites: meta.suites,
      };
    } catch {
      return summaryOf(runId, await loadReport(runId));
    }
  },
  cases: async (
    runId: string,
    suite: string,
    options: {
      status?: 'passed' | 'failed';
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<EvalCasePage> => {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (options.status) params.set('status', options.status);
    try {
      const page = await http.request<EvalCasePage>(
        '/api/eval/runs/' +
          encodeURIComponent(runId) +
          '/suites/' +
          encodeURIComponent(suite) +
          '/cases?' +
          params.toString(),
      );
      return {
        ...page,
        items: page.items.map((testCase) => ({ ...testCase, suite })),
      };
    } catch {
      const report = await loadReport(runId);
      const all = (report.suites.find((item) => item.name === suite)?.cases ?? [])
        .filter((testCase) => {
          if (options.status === 'passed') return testCase.ok;
          if (options.status === 'failed') return !testCase.ok;
          return true;
        })
        .map((testCase) => ({ ...testCase, suite }));
      return {
        items: all.slice(offset, offset + limit),
        total: all.length,
        limit,
        offset,
      };
    }
  },
  case: async (
    runId: string,
    suite: string,
    caseId: string,
  ): Promise<EvalCaseWithSuite> => {
    const params = new URLSearchParams({ suite, case_id: caseId });
    try {
      const testCase = await http.request<EvalCase>(
        '/api/eval/runs/' +
          encodeURIComponent(runId) +
          '/case?' +
          params.toString(),
      );
      return { ...testCase, suite };
    } catch {
      const report = await loadReport(runId);
      const found = flattenCases(report).find(
        (testCase) => testCase.suite === suite && testCase.id === caseId,
      );
      if (!found) throw new Error(`用例 ${caseId} 不存在`);
      return found;
    }
  },
  compare: async (base: string, candidate: string): Promise<EvalCompare> => {
    const params = new URLSearchParams({ base, candidate });
    return http.request<EvalCompare>('/api/eval/compare?' + params.toString());
  },
  datasets: async (): Promise<EvalDatasetList> => {
    return http.request<EvalDatasetList>('/api/eval/datasets');
  },
  dataset: async (name: string): Promise<EvalDataset> => {
    return http.request<EvalDataset>(
      '/api/eval/datasets/' + encodeURIComponent(name),
    );
  },
  baselines: async (): Promise<EvalBaselineList> => {
    return http.request<EvalBaselineList>('/api/eval/baselines');
  },
};

export function flattenCases(
  report: EvalReport | undefined,
): EvalCaseWithSuite[] {
  return (report?.suites ?? []).flatMap((suite) =>
    suite.cases.map((testCase) => ({ ...testCase, suite: suite.name })),
  );
}

export function findCase(
  report: EvalReport | undefined,
  caseId: string | null,
): EvalCaseWithSuite | undefined {
  if (!caseId) return undefined;
  return flattenCases(report).find((testCase) => testCase.id === caseId);
}
