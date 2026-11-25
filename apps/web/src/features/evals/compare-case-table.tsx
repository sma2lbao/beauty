/**
 * 用例差异表:按回归/修复/新增/移除过滤;点用例跳到对应运行详情。
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

import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import type { EvalCompareCase, EvalCompareStatus } from '@/lib/eval-api';

import { formatSigned, formatSignedDuration } from './format';
import type { EvalCompareFilter } from './use-evals-model';

const STATUS_META: Record<
  EvalCompareStatus,
  { label: string; tone: 'danger' | 'success' | 'info' | 'warning' | 'neutral' }
> = {
  regressed: { label: '回归', tone: 'danger' },
  fixed: { label: '修复', tone: 'success' },
  added: { label: '新增', tone: 'info' },
  removed: { label: '移除', tone: 'warning' },
  unchanged: { label: '未变', tone: 'neutral' },
};

const FILTERS: { value: EvalCompareFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'regressed', label: '回归' },
  { value: 'fixed', label: '修复' },
  { value: 'added', label: '新增' },
  { value: 'removed', label: '移除' },
];

export function CompareCaseTable({
  cases,
  total,
  status,
  onStatus,
  baseRunId,
  candidateRunId,
}: {
  cases: EvalCompareCase[];
  total: number;
  status: EvalCompareFilter;
  onStatus: (status: EvalCompareFilter) => void;
  baseRunId: string;
  candidateRunId: string;
}) {
  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">用例差异</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            先看回归,再看修复与集合变化;点用例跳到运行详情
          </p>
        </div>
        <div className="flex items-center gap-1">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              aria-pressed={status === filter.value}
              onClick={() => onStatus(filter.value)}
              className={
                'h-7 rounded-md px-2.5 text-[11px] transition-colors ' +
                (status === filter.value
                  ? 'bg-[#1c1d1f] font-semibold text-ha-text'
                  : 'text-ha-muted hover:text-ha-text-2')
              }
            >
              {filter.label}
            </button>
          ))}
          <span className="ml-2 font-mono text-[11px] text-ha-faint">
            {cases.length}/{total}
          </span>
        </div>
      </div>

      {cases.length === 0 ? (
        <EmptyPanel
          className="m-4 border-0"
          title="没有匹配的差异"
          description="换一个筛选条件,或两次运行的用例完全一致。"
        />
      ) : (
        <div className="overflow-x-auto">
          <Table density="compact">
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[240px]">用例</TableHead>
                <TableHead>套件</TableHead>
                <TableHead>变化</TableHead>
                <TableHead>基线</TableHead>
                <TableHead>候选</TableHead>
                <TableHead className="text-right">得分 Δ</TableHead>
                <TableHead className="text-right">tokens Δ</TableHead>
                <TableHead className="text-right">耗时 Δ</TableHead>
                <TableHead className="min-w-[180px]">失败原因</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cases.map((item) => {
                const meta = STATUS_META[item.status];
                const runId =
                  item.status === 'removed' ? baseRunId : candidateRunId;
                const href =
                  '/evals/runs/' +
                  runId +
                  '?suite=' +
                  encodeURIComponent(item.suite) +
                  '&case=' +
                  encodeURIComponent(item.case_id) +
                  '&tab=result';
                return (
                  <TableRow key={item.suite + '/' + item.case_id}>
                    <TableCell>
                      <Link
                        to={href}
                        className="font-mono text-[11px] text-ha-text hover:text-ha-accent-soft"
                      >
                        {item.case_id}
                      </Link>
                      <p className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-ha-faint">
                        {item.tags.slice(0, 3).map((tag) => (
                          <span key={tag}>#{tag}</span>
                        ))}
                      </p>
                    </TableCell>
                    <TableCell className="font-mono text-[10px] text-ha-muted">
                      {item.suite}
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
                    </TableCell>
                    <TableCell>
                      <OkCell value={item.base_ok} />
                    </TableCell>
                    <TableCell>
                      <OkCell value={item.candidate_ok} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                      {bothScores(item)
                        ? formatSigned(item.score_delta, 2)
                        : '—'}
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                      {formatSigned(item.tokens_delta)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                      {formatSignedDuration(item.duration_delta_ms)}
                    </TableCell>
                    <TableCell>
                      <span className="line-clamp-2 text-[11px] text-ha-danger">
                        {item.failures[0] ??
                          item.candidate_error ??
                          item.base_error ??
                          '—'}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </ConsolePanel>
  );
}

function OkCell({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-ha-faint">—</span>;
  return (
    <StatusBadge tone={value ? 'success' : 'danger'}>
      {value ? '通过' : '失败'}
    </StatusBadge>
  );
}

function bothScores(item: EvalCompareCase): boolean {
  return item.base_score !== null && item.candidate_score !== null;
}
