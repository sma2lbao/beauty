/**
 * 评测总览:一次运行的健康度快照。
 *
 * 顺序刻意是「失败/漂移 → 套件 → 历史 → 成本」:先回答能不能发,
 * 再回答哪个套件退化了,最后才看 tokens 与耗时。
 */
import type { EvalRunMeta } from '@/lib/eval-api';

import { AttentionPanel } from './attention-panel';
import { EvalKpiCards } from './eval-kpi-cards';
import { RunHistory } from './run-history';
import { SuiteHealthTable } from './suite-health-table';

type SuiteRow = EvalRunMeta['suites'][number] & {
  trend: (number | null)[];
};

export function EvalOverview({
  latest,
  runs,
  suiteRows,
}: {
  latest: EvalRunMeta;
  runs: EvalRunMeta[];
  suiteRows: SuiteRow[];
}) {
  return (
    <div className="space-y-4 px-6 pb-8 sm:px-8">
      <EvalKpiCards
        totals={latest.totals}
        suiteCount={latest.suites.length}
        driftCount={latest.drift_count}
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <SuiteHealthTable rows={suiteRows} runId={latest.id} />
          <RunHistory runs={runs} />
        </div>
        <AttentionPanel latest={latest} />
      </div>
    </div>
  );
}
