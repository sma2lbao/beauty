/**
 * 评测平台只读数据层(M0 静态快照版)。
 *
 * 数据来自 `public/eval/index.json` 与 `public/eval/runs/<id>.json`,
 * 由 `node scripts/eval/publish-web.mjs` 从 evalkit 的 JSON 报告发布。
 * 后续 eval-service 上线后只需替换这里的两条 URL,页面与类型不变。
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
  index: () => http.request<EvalIndex>('/eval/index.json'),
  run: (runId: string) =>
    http.request<EvalReport>(
      '/eval/runs/' + encodeURIComponent(runId) + '.json',
    ),
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
