/**
 * 评测 KPI 卡:结论优先(通过率/失败/漂移),成本与耗时次级。
 */
import {
  AlertTriangle,
  CheckCircle2,
  Coins,
  FlaskConical,
  Gauge,
  Timer,
} from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';
import type { EvalTotals } from '@/lib/eval-api';

import { formatDuration, formatNumber, formatPercent } from './format';

export function EvalKpiCards({
  totals,
  suiteCount,
  driftCount,
}: {
  totals: EvalTotals;
  suiteCount: number;
  driftCount: number;
}) {
  const stats = [
    {
      label: '通过率',
      value: formatPercent(totals.pass_rate),
      hint: totals.passed + '/' + totals.cases + ' 通过',
      icon: <CheckCircle2 className="h-4 w-4" />,
      tone: totals.failed > 0 ? 'text-ha-danger' : 'text-ha-success',
    },
    {
      label: '失败用例',
      value: formatNumber(totals.failed),
      hint: totals.failed > 0 ? '阻塞发布' : '没有阻塞',
      icon: <AlertTriangle className="h-4 w-4" />,
      tone: totals.failed > 0 ? 'text-ha-danger' : 'text-ha-muted',
    },
    {
      label: '漂移',
      value: formatNumber(driftCount),
      hint: '相对 approved baseline',
      icon: <Gauge className="h-4 w-4" />,
      tone: driftCount > 0 ? 'text-ha-warning' : 'text-ha-muted',
    },
    {
      label: '套件',
      value: formatNumber(suiteCount),
      hint: '本次运行',
      icon: <FlaskConical className="h-4 w-4" />,
      tone: 'text-ha-accent-soft',
    },
    {
      label: 'tokens',
      value: formatNumber(totals.tokens),
      hint: '脚本化模型估算',
      icon: <Coins className="h-4 w-4" />,
      tone: 'text-ha-accent-soft',
    },
    {
      label: '耗时',
      value: formatDuration(totals.duration_ms),
      hint: '总执行时间',
      icon: <Timer className="h-4 w-4" />,
      tone: 'text-ha-muted',
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {stats.map((stat) => (
        <ConsolePanel key={stat.label} className="p-4">
          <div className="flex items-center justify-between text-ha-muted">
            <span className="text-[12px]">{stat.label}</span>
            <span className={stat.tone}>{stat.icon}</span>
          </div>
          <p className="mt-2 font-mono text-[22px] font-semibold text-ha-text">
            {stat.value}
          </p>
          <p className="mt-1 truncate text-[11px] text-ha-faint">{stat.hint}</p>
        </ConsolePanel>
      ))}
    </div>
  );
}
