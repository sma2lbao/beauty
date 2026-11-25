/**
 * 跨运行对比:先回答"退化了什么",再看通过率与成本差值。
 *
 * 数据来自 eval-service 的 /v1/compare;用例差异表在 compare-case-table。
 */
import { ArrowRight, CheckCircle2, Minus, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
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
import type {
  EvalCompare,
  EvalCompareSuite,
  EvalRunMeta,
} from '@/lib/eval-api';

import { CompareCaseTable } from './compare-case-table';
import {
  deltaTone,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatShortSha,
  formatSigned,
  formatSignedDuration,
  formatSignedPercent,
} from './format';
import type { EvalCompareFilter } from './use-evals-model';

type CompareModel = {
  compare: EvalCompare;
  runs: EvalRunMeta[];
  baseId: string;
  candidateId: string;
  status: EvalCompareFilter;
  cases: EvalCompare['cases'];
};

export function CompareView({
  compare,
  runs,
  baseId,
  candidateId,
  status,
  cases,
  selectBase,
  selectCandidate,
  selectStatus,
}: CompareModel & {
  selectBase: (id: string) => void;
  selectCandidate: (id: string) => void;
  selectStatus: (status: EvalCompareFilter) => void;
}) {
  const { totals } = compare;

  return (
    <div className="space-y-4 px-6 pb-8 sm:px-8">
      <ConsolePanel className="p-4">
        <div className="grid gap-3 md:grid-cols-2">
          <RunPicker
            label="基线运行"
            value={baseId}
            runs={runs}
            onChange={selectBase}
          />
          <RunPicker
            label="候选运行"
            value={candidateId}
            runs={runs}
            onChange={selectCandidate}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ha-faint">
          <span>
            基线 {formatDateTime(compare.base.created_at)} ·{' '}
            {compare.base.branch}@{formatShortSha(compare.base.git_sha)}
          </span>
          <span>
            候选 {formatDateTime(compare.candidate.created_at)} ·{' '}
            {compare.candidate.branch}@
            {formatShortSha(compare.candidate.git_sha)}
          </span>
        </div>
      </ConsolePanel>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <CompareStat
          label="回归"
          value={formatNumber(totals.regressed)}
          hint="基线通过 → 候选失败"
          tone={totals.regressed > 0 ? 'danger' : 'neutral'}
          icon={<XCircle className="h-4 w-4" />}
        />
        <CompareStat
          label="修复"
          value={formatNumber(totals.fixed)}
          hint="基线失败 → 候选通过"
          tone={totals.fixed > 0 ? 'success' : 'neutral'}
          icon={<CheckCircle2 className="h-4 w-4" />}
        />
        <CompareStat
          label="新增 / 移除"
          value={
            formatNumber(totals.added) + ' / ' + formatNumber(totals.removed)
          }
          hint="用例集合变化"
          tone="neutral"
          icon={<Minus className="h-4 w-4" />}
        />
        <CompareStat
          label="通过率"
          value={formatPercent(totals.candidate_pass_rate)}
          hint={
            formatPercent(totals.base_pass_rate) +
            ' → ' +
            formatPercent(totals.candidate_pass_rate) +
            ' · ' +
            formatSignedPercent(totals.pass_rate_delta)
          }
          tone={deltaTone(totals.pass_rate_delta)}
          icon={<ArrowRight className="h-4 w-4" />}
        />
        <CompareStat
          label="tokens"
          value={formatSigned(totals.tokens_delta)}
          hint="候选相对基线的用量差"
          tone={deltaTone(totals.tokens_delta, true)}
          icon={<ArrowRight className="h-4 w-4" />}
        />
        <CompareStat
          label="耗时"
          value={formatSignedDuration(totals.duration_delta_ms)}
          hint="候选相对基线的耗时差"
          tone={deltaTone(totals.duration_delta_ms, true)}
          icon={<ArrowRight className="h-4 w-4" />}
        />
      </div>

      <CompareSuiteTable
        suites={compare.suites}
        candidateRunId={compare.candidate.id}
      />

      <CompareCaseTable
        cases={cases}
        total={compare.cases.length}
        status={status}
        onStatus={selectStatus}
        baseRunId={compare.base.id}
        candidateRunId={compare.candidate.id}
      />
    </div>
  );
}

function RunPicker({
  label,
  value,
  runs,
  onChange,
}: {
  label: string;
  value: string;
  runs: EvalRunMeta[];
  onChange: (id: string) => void;
}) {
  const known = runs.some((run) => run.id === value);
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-[0.08em] text-ha-faint">
        {label}
      </span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-9 w-full rounded-md border border-ha-border bg-ha-sidebar px-2.5 font-mono text-[11px] text-ha-text outline-none focus:border-ha-accent"
      >
        {!known && value ? <option value={value}>{value}</option> : null}
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            {run.id.slice(0, 8)} · {run.branch} ·{' '}
            {formatDateTime(run.created_at)}
          </option>
        ))}
      </select>
    </label>
  );
}

function CompareStat({
  label,
  value,
  hint,
  tone,
  icon,
}: {
  label: string;
  value: string;
  hint: string;
  tone: 'success' | 'danger' | 'neutral';
  icon: ReactNode;
}) {
  const color =
    tone === 'success'
      ? 'text-ha-success'
      : tone === 'danger'
        ? 'text-ha-danger'
        : 'text-ha-muted';
  return (
    <ConsolePanel className="p-4">
      <div className="flex items-center justify-between text-ha-muted">
        <span className="text-[12px]">{label}</span>
        <span className={color}>{icon}</span>
      </div>
      <p className={'mt-2 font-mono text-[22px] font-semibold ' + color}>
        {value}
      </p>
      <p className="mt-1 truncate text-[11px] text-ha-faint">{hint}</p>
    </ConsolePanel>
  );
}

function CompareSuiteTable({
  suites,
  candidateRunId,
}: {
  suites: EvalCompareSuite[];
  candidateRunId: string;
}) {
  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">套件差异</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            通过率变化与用例级增删;只在基线出现的套件也列出来
          </p>
        </div>
        <span className="font-mono text-[11px] text-ha-faint">
          {suites.length} 个套件
        </span>
      </div>
      <div className="overflow-x-auto">
        <Table density="compact">
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-[150px]">套件</TableHead>
              <TableHead className="text-right">基线</TableHead>
              <TableHead className="text-right">候选</TableHead>
              <TableHead className="text-right">通过率 Δ</TableHead>
              <TableHead className="text-right">回归</TableHead>
              <TableHead className="text-right">修复</TableHead>
              <TableHead className="text-right">新增 / 移除</TableHead>
              <TableHead className="w-[72px] text-right">详情</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {suites.map((suite) => {
              const baseRate = suite.base_totals?.pass_rate;
              const candidateRate = suite.candidate_totals?.pass_rate;
              const delta =
                baseRate !== undefined && candidateRate !== undefined
                  ? candidateRate - baseRate
                  : null;
              return (
                <TableRow key={suite.name}>
                  <TableCell className="text-[12px] font-medium text-ha-text">
                    {suite.name}
                  </TableCell>
                  <TableCell className="text-right">
                    <PassRate totals={suite.base_totals} />
                  </TableCell>
                  <TableCell className="text-right">
                    <PassRate totals={suite.candidate_totals} />
                  </TableCell>
                  <TableCell className="text-right">
                    {delta === null ? (
                      <span className="text-ha-faint">—</span>
                    ) : (
                      <span
                        className={'font-mono text-[11px] ' + toneText(delta)}
                      >
                        {formatSignedPercent(delta)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Count value={suite.regressed} tone="danger" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Count value={suite.fixed} tone="success" />
                  </TableCell>
                  <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                    {suite.added} / {suite.removed}
                  </TableCell>
                  <TableCell className="text-right">
                    <Link
                      to={
                        '/evals/runs/' +
                        candidateRunId +
                        '?suite=' +
                        encodeURIComponent(suite.name)
                      }
                      className="text-[11px] text-ha-muted hover:text-ha-text"
                    >
                      查看
                    </Link>
                  </TableCell>
                </TableRow>
              );
            })}
            {suites.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className="py-8 text-center text-[12px] text-ha-faint"
                >
                  两次运行都没有套件数据。
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </ConsolePanel>
  );
}

function PassRate({ totals }: { totals: EvalCompareSuite['base_totals'] }) {
  if (!totals) return <span className="text-ha-faint">—</span>;
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

function Count({ value, tone }: { value: number; tone: 'danger' | 'success' }) {
  if (value === 0) {
    return <span className="font-mono text-[11px] text-ha-faint">0</span>;
  }
  return <StatusBadge tone={tone}>{value}</StatusBadge>;
}

function toneText(delta: number): string {
  if (delta > 0) return 'text-ha-success';
  if (delta < 0) return 'text-ha-danger';
  return 'text-ha-muted';
}
