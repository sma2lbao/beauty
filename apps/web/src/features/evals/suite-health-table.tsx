/**
 * 套件健康度:一次运行里每个套件的通过/漂移/成本与最近趋势。
 */
import { Link } from 'react-router';

import {
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@beauty/design';

import { ConsolePanel } from '@/components/console/ui';
import type { EvalSuiteSummary, EvalTotals } from '@/lib/eval-api';

import { formatNumber, formatPercent } from './format';
import { Sparkline } from './sparkline';

type SuiteRow = EvalSuiteSummary & {
  trend: (number | null)[];
};

export function SuiteHealthTable({
  rows,
  runId,
}: {
  rows: SuiteRow[];
  runId: string;
}) {
  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">套件健康度</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            先看失败与漂移,再看平均分
          </p>
        </div>
        <span className="font-mono text-[11px] text-ha-faint">
          {rows.length} 个套件
        </span>
      </div>
      <div className="overflow-x-auto">
        <Table density="compact">
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-[150px]">套件</TableHead>
              <TableHead className="text-right">用例</TableHead>
              <TableHead className="text-right">通过</TableHead>
              <TableHead className="text-right">漂移</TableHead>
              <TableHead className="text-right">得分</TableHead>
              <TableHead className="text-right">tokens</TableHead>
              <TableHead className="w-[84px]">趋势</TableHead>
              <TableHead className="w-[72px] text-right">详情</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.name}>
                <TableCell>
                  <Link
                    to={
                      '/evals/runs/' +
                      runId +
                      '?suite=' +
                      encodeURIComponent(row.name)
                    }
                    className="text-[12px] font-medium text-ha-text hover:text-ha-accent-soft"
                  >
                    {row.name}
                  </Link>
                  <p className="mt-0.5 font-mono text-[10px] text-ha-faint">
                    {row.dataset ?? 'dataset 未声明'} · v
                    {row.dataset_version ?? '?'}
                  </p>
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                  {formatNumber(row.totals.cases)}
                </TableCell>
                <TableCell className="text-right">
                  <PassCell totals={row.totals} />
                </TableCell>
                <TableCell className="text-right">
                  <DriftCell count={row.drift_count} />
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                  {row.totals.score.toFixed(2)}
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                  {formatNumber(row.totals.tokens)}
                </TableCell>
                <TableCell>
                  <Sparkline
                    values={row.trend}
                    label={row.name + ' 通过率趋势'}
                  />
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    to={
                      '/evals/runs/' +
                      runId +
                      '?suite=' +
                      encodeURIComponent(row.name)
                    }
                    className="text-[11px] text-ha-muted hover:text-ha-text"
                  >
                    查看
                  </Link>
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className="py-8 text-center text-[12px] text-ha-faint"
                >
                  本次运行没有套件数据。
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </ConsolePanel>
  );
}

function PassCell({ totals }: { totals: EvalTotals }) {
  const tone =
    totals.failed > 0 ? 'danger' : totals.pass_rate < 1 ? 'warning' : 'success';
  return (
    <span className="inline-flex items-center gap-2">
      <StatusBadge tone={tone}>{formatPercent(totals.pass_rate)}</StatusBadge>
      <span className="font-mono text-[10px] text-ha-faint">
        {totals.passed}/{totals.cases}
      </span>
    </span>
  );
}

function DriftCell({ count }: { count: number }) {
  if (count === 0) {
    return <span className="font-mono text-[11px] text-ha-faint">0</span>;
  }
  return <StatusBadge tone="warning">{count}</StatusBadge>;
}
