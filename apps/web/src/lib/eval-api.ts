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

const http = createHttpClient('');

export const evalApi = {
  index: async (): Promise<EvalIndex> => {
    try {
      return await http.request<EvalIndex>('/api/eval/runs?limit=5');
    } catch {
      return http.request<EvalIndex>('/eval/index.json');
    }
  },
  run: async (runId: string): Promise<EvalReport> => {
    try {
      return await http.request<EvalReport>(
        '/api/eval/runs/' + encodeURIComponent(runId),
      );
    } catch {
      return http.request<EvalReport>(
        '/eval/runs/' + encodeURIComponent(runId) + '.json',
      );
    }
  },
};

/** 运行详情里的用例:带 suite 便于跨套件定位。 */
export type EvalCaseWithSuite = EvalCase & { suite: string };

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
