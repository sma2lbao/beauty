import { describe, expect, it } from 'vitest';

import type { EvalBaseline, EvalRunMeta } from '@/lib/eval-api';

import { buildGateRows, gateStatus } from './gates';

const NOW = Date.parse('2026-11-25T12:00:00Z');

function baseline(overrides: Partial<EvalBaseline> = {}): EvalBaseline {
  return {
    suite: 'agent-core',
    dataset_version: 1,
    evaluator_version: '1',
    git_sha: 'abcdef1',
    approved_by: 'alice',
    approved_at: '2026-11-20T12:00:00Z',
    case_count: 2,
    passed: 2,
    ...overrides,
  };
}

function run(version: number | null, cases: number): EvalRunMeta {
  const totals = {
    cases,
    passed: cases,
    failed: 0,
    pass_rate: 1,
    score: 1,
    tokens: 10,
    duration_ms: 5,
  };
  return {
    id: 'run-1',
    created_at: '2026-11-25T10:00:00Z',
    trigger: 'ci',
    git_sha: 'abcdef1',
    branch: 'main',
    pr: null,
    model: 'eval-scripted',
    environment: 'ci',
    report_version: 1,
    totals,
    drift_count: 0,
    suites: [
      {
        name: 'agent-core',
        dataset: 'agent-core',
        dataset_version: version,
        totals,
        drift_count: 0,
      },
    ],
    failed_cases: [],
    dataset_versions: { 'agent-core': version },
  };
}

describe('gateStatus', () => {
  it('基线绑定完整且新鲜时就绪', () => {
    expect(gateStatus(baseline(), run(1, 2).suites[0], 5)).toBe('ready');
  });

  it('缺基线、未绑定版本、无对应运行优先暴露', () => {
    expect(gateStatus(null, run(1, 2).suites[0], null)).toBe('missing');
    expect(gateStatus(baseline({ dataset_version: null }), null, 5)).toBe(
      'unbound',
    );
    expect(gateStatus(baseline(), null, 5)).toBe('no-run');
  });

  it('版本或用例数变化不可比,超过 30 天视为过期', () => {
    expect(gateStatus(baseline(), run(2, 2).suites[0], 5)).toBe(
      'version-mismatch',
    );
    expect(gateStatus(baseline(), run(1, 3).suites[0], 5)).toBe(
      'case-mismatch',
    );
    expect(gateStatus(baseline(), run(1, 2).suites[0], 31)).toBe('stale');
  });
});

describe('buildGateRows', () => {
  it('合并且只出现一侧的套件,缺基线排在前按名称排序', () => {
    const latest = run(1, 2);
    latest.suites.push({
      ...latest.suites[0],
      name: 'memory-quality',
      dataset: 'memory-quality',
    });
    const rows = buildGateRows([baseline()], latest, NOW);

    expect(rows.map((row) => row.suite)).toEqual([
      'agent-core',
      'memory-quality',
    ]);
    expect(rows[0].status).toBe('ready');
    expect(rows[1].status).toBe('missing');
    expect(rows[0].ageDays).toBe(5);
  });
});
