/**
 * 门禁状态推导:基线是否仍能代表当前数据集与运行。
 *
 * 规则优先级(命中即停):missing > unbound > no-run > version-mismatch
 * > case-mismatch > stale > ready。缺用例不能算通过,过期基线不能算门禁,
 * 因此都排在 ready 之前。
 */
import type { EvalBaseline, EvalRunMeta } from '@/lib/eval-api';

export const BASELINE_MAX_AGE_DAYS = 30;

export type EvalGateStatus =
  | 'missing'
  | 'unbound'
  | 'no-run'
  | 'version-mismatch'
  | 'case-mismatch'
  | 'stale'
  | 'ready';

export type EvalGateRow = {
  suite: string;
  baseline: EvalBaseline | null;
  latestSuite: EvalRunMeta['suites'][number] | null;
  ageDays: number | null;
  status: EvalGateStatus;
};

export function gateStatus(
  baseline: EvalBaseline | null,
  latestSuite: EvalRunMeta['suites'][number] | null,
  ageDays: number | null,
): EvalGateStatus {
  if (!baseline) return 'missing';
  if (baseline.dataset_version === null) return 'unbound';
  if (!latestSuite) return 'no-run';
  if (latestSuite.dataset_version !== baseline.dataset_version) {
    return 'version-mismatch';
  }
  if (latestSuite.totals.cases !== baseline.case_count) {
    return 'case-mismatch';
  }
  if (ageDays === null || ageDays > BASELINE_MAX_AGE_DAYS) return 'stale';
  return 'ready';
}

export function buildGateRows(
  baselines: EvalBaseline[],
  latest: EvalRunMeta | undefined,
  now = Date.now(),
): EvalGateRow[] {
  const bySuite = new Map(baselines.map((item) => [item.suite, item]));
  const names = new Set<string>([
    ...baselines.map((item) => item.suite),
    ...(latest?.suites ?? []).map((item) => item.name),
  ]);

  return [...names].sort().map((suite) => {
    const baseline = bySuite.get(suite) ?? null;
    const latestSuite =
      latest?.suites.find((item) => item.name === suite) ?? null;
    const ageDays = baseline ? ageInDays(baseline.approved_at, now) : null;
    return {
      suite,
      baseline,
      latestSuite,
      ageDays,
      status: gateStatus(baseline, latestSuite, ageDays),
    };
  });
}

function ageInDays(iso: string, now: number): number | null {
  const timestamp = Date.parse(iso);
  if (Number.isNaN(timestamp)) return null;
  return Math.max(0, Math.floor((now - timestamp) / 86_400_000));
}
