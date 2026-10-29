/**
 * 四张统计卡:今日/本月 token 与请求次数。
 */
import { Activity, Cpu, Gauge, Sparkles } from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';
import type { UsageSummary } from '@/lib/insights-api';

import { formatNumber } from './format-number';

export function UsageStatCards({
  summary,
}: {
  summary: UsageSummary | undefined;
}) {
  const stats = [
    {
      label: '今日 tokens',
      value: summary?.today_tokens ?? 0,
      icon: <Sparkles className="h-4 w-4" />,
    },
    {
      label: '本月 tokens',
      value: summary?.month_tokens ?? 0,
      icon: <Gauge className="h-4 w-4" />,
    },
    {
      label: '今日请求',
      value: summary?.today_events ?? 0,
      icon: <Activity className="h-4 w-4" />,
    },
    {
      label: '本月请求',
      value: summary?.month_events ?? 0,
      icon: <Cpu className="h-4 w-4" />,
    },
  ];

  return (
    <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
      {stats.map((stat) => (
        <ConsolePanel key={stat.label} className="p-4">
          <div className="flex items-center justify-between text-ha-muted">
            <span className="text-[12px]">{stat.label}</span>
            <span className="text-ha-accent-soft">{stat.icon}</span>
          </div>
          <p className="mt-2 font-mono text-[24px] font-semibold text-ha-text">
            {formatNumber(stat.value)}
          </p>
        </ConsolePanel>
      ))}
    </div>
  );
}
