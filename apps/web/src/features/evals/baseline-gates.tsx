/**
 * 门禁治理:已批准基线是否仍绑定当前数据集版本与完整用例集。
 *
 * 只读页面——批准要求账号身份,本地 AUTH_ENABLED=false 时会 401,
 * 因此按钮不在这里做;批准走 POST /v1/baselines/{suite}。
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
import type { Tone } from '@beauty/design';

import { ConsolePanel } from '@/components/console/ui';

import { formatDateTime, formatNumber, formatShortSha } from './format';
import type { EvalGateRow, EvalGateStatus } from './gates';

const GATE_LABELS: Record<EvalGateStatus, string> = {
  missing: '缺基线',
  unbound: '未绑定版本',
  'no-run': '无运行',
  'version-mismatch': '版本不一致',
  'case-mismatch': '用例集不一致',
  stale: '已过期',
  ready: '就绪',
};

const GATE_TONES: Record<EvalGateStatus, Tone> = {
  missing: 'danger',
  unbound: 'warning',
  'no-run': 'neutral',
  'version-mismatch': 'danger',
  'case-mismatch': 'warning',
  stale: 'warning',
  ready: 'success',
};

export function BaselineGates({
  rows,
  runId,
}: {
  rows: EvalGateRow[];
  runId: string | undefined;
}) {
  return (
    <div className="space-y-4 px-6 pb-8 sm:px-8">
      <ConsolePanel className="overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
          <div>
            <h2 className="text-[13px] font-semibold text-ha-text">基线门禁</h2>
            <p className="mt-0.5 text-[11px] text-ha-faint">
              基线必须绑定数据集版本、完整用例集,并在 30 天内批准
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
                <TableHead className="min-w-[160px]">套件</TableHead>
                <TableHead>已批准基线</TableHead>
                <TableHead>最新运行</TableHead>
                <TableHead>批准</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-[72px] text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.suite}>
                  <TableCell>
                    <span className="font-mono text-[12px] text-ha-text">
                      {row.suite}
                    </span>
                  </TableCell>
                  <TableCell>
                    <BaselineCell row={row} />
                  </TableCell>
                  <TableCell>
                    <LatestCell row={row} />
                  </TableCell>
                  <TableCell>
                    <ApprovalCell row={row} />
                  </TableCell>
                  <TableCell>
                    <StatusBadge tone={GATE_TONES[row.status]}>
                      {GATE_LABELS[row.status]}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className="text-right">
                    {runId && row.latestSuite ? (
                      <Link
                        to={
                          '/evals/runs/' +
                          runId +
                          '?suite=' +
                          encodeURIComponent(row.suite)
                        }
                        className="text-[11px] text-ha-muted hover:text-ha-text"
                      >
                        查看
                      </Link>
                    ) : (
                      <span className="text-[11px] text-ha-faint">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-8 text-center text-[12px] text-ha-faint"
                  >
                    还没有基线或运行记录。
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      </ConsolePanel>

      <ConsolePanel className="px-4 py-3">
        <h2 className="text-[13px] font-semibold text-ha-text">判定规则</h2>
        <ul className="mt-2 space-y-1.5 text-[11px] leading-relaxed text-ha-muted">
          <li>缺基线 / 未绑定 dataset_version / 无对应运行 → 不能作为门禁;</li>
          <li>
            最新运行的数据集版本或用例数与基线不一致 → 不可比,缺用例不能算通过;
          </li>
          <li>批准超过 30 天视为过期,需要重新批准才能代表当前质量;</li>
          <li>
            CI 门禁始终以{' '}
            <code className="text-ha-text-2">
              bash scripts/eval/platform.sh run
            </code>{' '}
            的退出码为准;本页用于解释「为什么不能信这个基线」。
          </li>
          <li>
            批准接口:{' '}
            <code className="text-ha-text-2">
              POST /api/eval/baselines/{'{suite}'}
            </code>{' '}
            (要求账号身份,并写入 approved_by/approved_at)。
          </li>
        </ul>
      </ConsolePanel>
    </div>
  );
}

function BaselineCell({ row }: { row: EvalGateRow }) {
  if (!row.baseline) {
    return <span className="text-[11px] text-ha-faint">未批准</span>;
  }
  const { baseline } = row;
  return (
    <div>
      <p className="font-mono text-[11px] text-ha-text-2">
        v{baseline.dataset_version ?? '?'} · {formatNumber(baseline.case_count)}{' '}
        条
      </p>
      <p className="mt-0.5 font-mono text-[10px] text-ha-faint">
        eval {baseline.evaluator_version} · {formatShortSha(baseline.git_sha)}
      </p>
    </div>
  );
}

function LatestCell({ row }: { row: EvalGateRow }) {
  if (!row.latestSuite) {
    return <span className="text-[11px] text-ha-faint">最新运行无此套件</span>;
  }
  return (
    <div>
      <p className="font-mono text-[11px] text-ha-text-2">
        v{row.latestSuite.dataset_version ?? '?'} ·{' '}
        {formatNumber(row.latestSuite.totals.cases)} 条
      </p>
      <p className="mt-0.5 font-mono text-[10px] text-ha-faint">
        {formatNumber(row.latestSuite.totals.passed)} 条通过
      </p>
    </div>
  );
}

function ApprovalCell({ row }: { row: EvalGateRow }) {
  if (!row.baseline) {
    return <span className="text-[11px] text-ha-faint">—</span>;
  }
  return (
    <div>
      <p className="text-[11px] text-ha-muted">
        {row.baseline.approved_by || '未知'}
      </p>
      <p className="mt-0.5 font-mono text-[10px] text-ha-faint">
        {formatDateTime(row.baseline.approved_at)} ·{' '}
        {row.ageDays === null ? '—' : row.ageDays + ' 天前'}
      </p>
    </div>
  );
}