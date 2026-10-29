/**
 * 按模型用量卡片:本月 token/次数聚合;无数据时给明确空态。
 */
import { ConsolePanel, EmptyPanel, ToneBadge } from '@/components/console/ui';
import type { UsageByModel } from '@/lib/insights-api';

import { formatNumber } from './format-number';

export function ModelUsagePanel({ byModel }: { byModel: UsageByModel[] }) {
  return (
    <ConsolePanel className="p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-semibold text-ha-text-2">按模型</h2>
        <span className="font-mono text-[11px] text-ha-faint">本月聚合</span>
      </div>
      {byModel.length ? (
        <ul className="mt-3 divide-y divide-ha-border">
          {byModel.map((item) => (
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
  );
}
