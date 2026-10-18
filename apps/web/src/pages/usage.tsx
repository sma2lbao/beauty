/**
 * 用量只读页(M0-11 本地版):今日/本月 token、请求次数、按模型分布。
 * 无数据时给明确空态;数据由 usage_events 聚合(本地先由 seed/内部上报)。
 */
import { useQuery } from '@tanstack/react-query';
import { Activity, Cpu, Gauge, Sparkles } from 'lucide-react';

import {
  ConsolePanel,
  EmptyPanel,
  PageHeader,
  ToneBadge,
} from '@/components/console/ui';
import { insightsApi } from '@/lib/insights-api';

function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value);
}

export function UsagePage() {
  const summaryQuery = useQuery({
    queryKey: ['usage-summary'],
    queryFn: insightsApi.usage,
  });
  const modelsQuery = useQuery({
    queryKey: ['models'],
    queryFn: insightsApi.models,
    retry: false,
  });

  const summary = summaryQuery.data;
  const models = modelsQuery.data?.data ?? [];
  const error = summaryQuery.error;

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
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="用量"
        subtitle="今日 / 本月 token 与请求次数;模型调用统一经 llm-gateway,按租户聚合"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {error ? (
          <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
            读取用量失败:{error.message}
          </ConsolePanel>
        ) : null}

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

        <div className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
          <ConsolePanel className="p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-[13px] font-semibold text-ha-text-2">
                按模型
              </h2>
              <span className="font-mono text-[11px] text-ha-faint">
                本月聚合
              </span>
            </div>
            {summary?.by_model.length ? (
              <ul className="mt-3 divide-y divide-ha-border">
                {summary.by_model.map((item) => (
                  <li
                    key={item.model}
                    className="flex items-center justify-between gap-3 py-2.5"
                  >
                    <span className="truncate font-mono text-[12px] text-ha-text-2">
                      {item.model}
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="font-mono text-[12px] text-ha-text">
                        {formatNumber(item.tokens)} tokens
                      </span>
                      <ToneBadge tone="neutral">{item.events} 次</ToneBadge>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyPanel
                title="还没有用量数据"
                description="运行任务后会产生 token 记录;本地可先执行 npm run dev:seed 写入演示数据。"
              />
            )}
          </ConsolePanel>

          <ConsolePanel className="p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-[13px] font-semibold text-ha-text-2">
                可用模型
              </h2>
              <span className="font-mono text-[11px] text-ha-faint">
                llm-gateway
              </span>
            </div>
            {models.length ? (
              <ul className="mt-3 space-y-2">
                {models.map((model) => (
                  <li
                    key={model.id}
                    className="flex items-center justify-between gap-2 rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
                  >
                    <span className="truncate font-mono text-[12px] text-ha-text-2">
                      {model.id}
                    </span>
                    {model.id === modelsQuery.data?.default_model ? (
                      <ToneBadge tone="success">默认</ToneBadge>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-[12px] text-ha-dim">
                模型列表暂不可用(检查 llm-gateway)。
              </p>
            )}
          </ConsolePanel>
        </div>
      </div>
    </div>
  );
}
