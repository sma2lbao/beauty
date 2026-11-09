/**
 * 六张统计卡:今日/本月 token、请求次数与估算成本。
 */
import {
  Activity,
  CircleDollarSign,
  Cpu,
  Gauge,
  Sparkles,
  Wallet,
} from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';
import type { UsageSummary } from '@/lib/insights-api';

import { formatUsd } from '@/lib/presentation';

import { formatNumber } from './format-number';

export function UsageStatCards({
  summary,
}: {
  summary: UsageSummary | undefined;
}) {
  const stats = [
    {
      label: '今日 tokens',
      value: formatNumber(summary?.today_tokens ?? 0),
      icon: <Sparkles className="h-4 w-4" />,
    },
    {
      label: '本月 tokens',
      value: formatNumber(summary?.month_tokens ?? 0),
      icon: <Gauge className="h-4 w-4" />,
    },
    {
      label: '今日请求',
      value: formatNumber(summary?.today_events ?? 0),
      icon: <Activity className="h-4 w-4" />,
    },
    {
      label: '本月请求',
      value: formatNumber(summary?.month_events ?? 0),
      icon: <Cpu className="h-4 w-4" />,
    },
    {
      label: '今日成本',
      value: formatUsd(summary?.today_cost_usd ?? 0),
      icon: <CircleDollarSign className="h-4 w-4" />,
    },
    {
      label: '本月成本',
      value: formatUsd(summary?.month_cost_usd ?? 0),
      icon: <Wallet className="h-4 w-4" />,
    },
  ];

  const unpriced = summary?.unpriced_events ?? 0;

  return (
    <div className="mt-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {stats.map((stat) => (
          <ConsolePanel key={stat.label} className="p-4">
            <div className="flex items-center justify-between text-ha-muted">
              <span className="text-[12px]">{stat.label}</span>
              <span className="text-ha-accent-soft">{stat.icon}</span>
            </div>
            <p className="mt-2 font-mono text-[24px] font-semibold text-ha-text">
              {stat.value}
            </p>
          </ConsolePanel>
        ))}
      </div>
      {unpriced > 0 ? (
        <p className="mt-2 text-[11px] text-ha-muted">
          本月有 {formatNumber(unpriced)} 次调用未计价(价目表缺项或 BYOK
          自定义模型),金额按 0 计入。
        </p>
      ) : null}
    </div>
  );
}
