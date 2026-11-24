/**
 * 最近运行:运行索引里的历史列表,保留进入详情与原始快照的入口。
 */
import { ExternalLink } from 'lucide-react';
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
import type { EvalRunMeta } from '@/lib/eval-api';

import {
  formatDateTime,
  formatNumber,
  formatPercent,
  formatShortSha,
} from './format';

export function RunHistory({ runs }: { runs: EvalRunMeta[] }) {
  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">最近运行</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            同一份数据集的连续快照;点进运行看逐用例结果
          </p>
        </div>
        <span className="font-mono text-[11px] text-ha-faint">
          {runs.length} 次
        </span>
      </div>
      <div className="overflow-x-auto">
        <Table density="compact">
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-[180px]">运行</TableHead>
              <TableHead>时间</TableHead>
              <TableHead>分支 / SHA</TableHead>
              <TableHead>触发</TableHead>
              <TableHead className="text-right">通过率</TableHead>
              <TableHead className="text-right">tokens</TableHead>
              <TableHead className="w-[72px] text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell>
                  <Link
                    to={'/evals/runs/' + run.id}
                    className="font-mono text-[11px] text-ha-text hover:text-ha-accent-soft"
                  >
                    {run.id}
                  </Link>
                  <p className="mt-0.5 text-[10px] text-ha-faint">
                    {run.totals.cases} 条用例 · {run.suites.length} 个套件
                  </p>
                </TableCell>
                <TableCell className="whitespace-nowrap text-[11px] text-ha-muted">
                  {formatDateTime(run.created_at)}
                </TableCell>
                <TableCell className="font-mono text-[10px] text-ha-muted">
                  {run.branch}@{formatShortSha(run.git_sha)}
                </TableCell>
                <TableCell>
                  <StatusBadge tone="neutral">{run.trigger}</StatusBadge>
                </TableCell>
                <TableCell className="text-right">
                  <StatusBadge
                    tone={run.totals.failed > 0 ? 'danger' : 'success'}
                  >
                    {formatPercent(run.totals.pass_rate)}
                  </StatusBadge>
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                  {formatNumber(run.totals.tokens)}
                </TableCell>
                <TableCell className="text-right">
                  <a
                    href={'/eval/runs/' + encodeURIComponent(run.id) + '.json'}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-ha-muted hover:text-ha-text"
                  >
                    JSON
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </TableCell>
              </TableRow>
            ))}
            {runs.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="py-8 text-center text-[12px] text-ha-faint"
                >
                  还没有运行快照。
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </ConsolePanel>
  );
}
