import { Alert, Button, Spin } from '@beauty/design';
import type { ReactNode } from 'react';

import { ConsolePanel, EmptyPanel, ToneBadge } from '@/components/console/ui';
import type {
  AdminCostByDay,
  AdminCostByModel,
  AdminCostBySource,
  AdminCostSummary,
  AdminCostTopAccount,
} from '@/lib/admin-api';
import { formatUsd } from '@/lib/presentation';

import { formatNumber } from '../usage/format-number';
import { AdminAccessNotice } from './admin-access-panel';
import {
  ADMIN_COST_RANGES,
  type AdminCostModel,
} from './use-admin-cost-model';

const SOURCE_LABELS: Record<AdminCostBySource['source'], string> = {
  platform: '平台承担',
  byok: 'BYOK(自带 key)',
};

function StatCard({
  label,
  value,
  hint,
  badge,
  testId,
}: {
  label: string;
  value: string;
  hint?: string;
  badge?: ReactNode;
  testId?: string;
}) {
  return (
    <ConsolePanel className="p-4">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-ha-dim">{label}</span>
        {badge ? (
          <span data-testid={testId ? testId + '-badge' : undefined}>
            {badge}
          </span>
        ) : null}
      </div>
      <div
        className="mt-1.5 font-mono text-lg text-ha-text"
        data-testid={testId}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-0.5 text-[11px] text-ha-faint">{hint}</div>
      ) : null}
    </ConsolePanel>
  );
}

/** 来源拆分:成本 + 占窗口总成本的比例条。 */
function SourceSplit({ summary }: { summary: AdminCostSummary }) {
  const totalCost = summary.totals.cost_usd;
  return (
    <ConsolePanel className="mt-4 p-4">
      <h3 className="text-[12px] font-medium text-ha-text-2">来源拆分</h3>
      <ul className="mt-3 space-y-3">
        {summary.cost_by_source.map((item) => {
          const share = totalCost > 0 ? (item.cost_usd / totalCost) * 100 : 0;
          return (
            <li
              key={item.source}
              data-testid={'admin-cost-source-' + item.source}
            >
              <div className="flex items-center justify-between gap-3 text-[12px]">
                <span className="text-ha-text-2">{SOURCE_LABELS[item.source]}</span>
                <span className="font-mono text-ha-dim">
                  {formatNumber(item.events)} 次 · {formatUsd(item.cost_usd)}
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ha-panel-2">
                <div
                  className="h-full rounded-full bg-ha-accent"
                  style={{ width: share.toFixed(1) + '%' }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </ConsolePanel>
  );
}

/** 按天成本:CSS 条形图,高度按窗口内最大成本归一;title 给日期与金额。 */
function DailyBars({ days }: { days: AdminCostByDay[] }) {
  const maxCost = days.reduce((max, day) => Math.max(max, day.cost_usd), 0);
  return (
    <ConsolePanel className="mt-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[12px] font-medium text-ha-text-2">按天成本</h3>
        <span className="text-[11px] text-ha-faint">
          条形高度按窗口内最大成本归一
        </span>
      </div>
      {days.length === 0 ? (
        <EmptyPanel
          className="mt-3"
          title="窗口内没有成本数据"
          description="换一个时间范围,或等平台产生调用后再来看。"
        />
      ) : (
        <>
          <div
            className="mt-4 flex h-24 items-end gap-[2px]"
            role="img"
            aria-label="按天成本条形图"
          >
            {days.map((day) => {
              const percent =
                maxCost > 0 ? Math.max(2, (day.cost_usd / maxCost) * 100) : 0;
              const title =
                day.date +
                ' · ' +
                formatUsd(day.cost_usd) +
                ' · ' +
                formatNumber(day.events) +
                ' 次调用';
              return (
                <div
                  key={day.date}
                  data-testid="admin-cost-day-bar"
                  title={title}
                  aria-label={title}
                  className="min-w-0 flex-1 rounded-t-[2px] bg-ha-accent"
                  style={{ height: percent + '%' }}
                />
              );
            })}
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-ha-faint">
            <span>{days[0].date}</span>
            <span>{days[days.length - 1].date}</span>
          </div>
        </>
      )}
    </ConsolePanel>
  );
}

/** 按模型表:服务端已按 cost_usd 降序取前 20。 */
function ModelTable({ models }: { models: AdminCostByModel[] }) {
  return (
    <ConsolePanel className="mt-4 overflow-x-auto">
      <h3 className="border-b border-ha-border px-4 py-2.5 text-[12px] font-medium text-ha-text-2">
        按模型
      </h3>
      {models.length === 0 ? (
        <p className="px-4 py-6 text-center text-[12px] text-ha-dim">
          窗口内没有模型调用记录。
        </p>
      ) : (
        <table className="w-full min-w-[520px] text-left text-[12px]">
          <thead className="border-b border-ha-border text-[11px] text-ha-dim">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">
                模型
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                调用
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                tokens
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                成本
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                未计价
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ha-border">
            {models.map((item) => (
              <tr key={item.model} data-testid="admin-cost-model-row">
                <td className="px-4 py-2.5 font-mono text-ha-text-2">
                  {item.model}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-dim">
                  {formatNumber(item.events)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-dim">
                  {formatNumber(item.tokens)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-text-2">
                  {formatUsd(item.cost_usd)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-dim">
                  {formatNumber(item.unpriced_events)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ConsolePanel>
  );
}

/** Top 账号:email 是「能查到就显示」,缺失时退回账号 id 前缀。 */
function TopAccounts({ accounts }: { accounts: AdminCostTopAccount[] }) {
  return (
    <ConsolePanel className="mt-4 overflow-x-auto">
      <h3 className="border-b border-ha-border px-4 py-2.5 text-[12px] font-medium text-ha-text-2">
        Top 账号
      </h3>
      {accounts.length === 0 ? (
        <p className="px-4 py-6 text-center text-[12px] text-ha-dim">
          窗口内没有账号成本记录。
        </p>
      ) : (
        <table className="w-full min-w-[520px] text-left text-[12px]">
          <thead className="border-b border-ha-border text-[11px] text-ha-dim">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">
                账号
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                调用
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                tokens
              </th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                成本
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ha-border">
            {accounts.map((item) => (
              <tr key={item.account_id} data-testid="admin-cost-account-row">
                <td
                  className="px-4 py-2.5 font-mono text-ha-text-2"
                  title={item.account_id}
                >
                  {item.email ?? item.account_id.slice(0, 8) + '…'}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-dim">
                  {formatNumber(item.events)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-dim">
                  {formatNumber(item.tokens)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-ha-text-2">
                  {formatUsd(item.cost_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ConsolePanel>
  );
}

/**
 * 成本页签(P1-K):范围 7/30/90 天、总量统计卡、来源拆分、按天条形图、
 * 按模型表与 Top 账号表。契约 docs/design/eval-and-cost-dashboard.md §4。
 */
export function AdminCostPanel({ model }: { model: AdminCostModel }) {
  if (model.access) {
    return <AdminAccessNotice access={model.access} onRetry={model.refetch} />;
  }
  if (model.error) {
    return (
      <Alert variant="danger" className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>读取成本汇总失败:{(model.error as Error).message}</span>
          <Button variant="outline" size="sm" onClick={model.refetch}>
            重试
          </Button>
        </div>
      </Alert>
    );
  }
  if (model.isLoading) {
    return (
      <div className="mt-6">
        <Spin label="加载成本汇总…" />
      </div>
    );
  }
  const data = model.data;
  if (!data) {
    return (
      <EmptyPanel
        className="mt-6"
        title="暂无成本数据"
        description="平台还没有产生调用记录。"
      />
    );
  }
  const totals = data.totals;
  return (
    <div data-testid="admin-cost-panel">
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div
          className="flex items-center gap-1.5"
          role="group"
          aria-label="成本统计范围"
        >
          {ADMIN_COST_RANGES.map((days) => (
            <Button
              key={days}
              variant={model.range === days ? 'primary' : 'outline'}
              size="sm"
              aria-pressed={model.range === days}
              onClick={() => model.setRange(days)}
            >
              最近 {days} 天
            </Button>
          ))}
        </div>
        <span className="font-mono text-[11px] text-ha-dim">
          UTC 自然日 · 自 {data.since.slice(0, 10)} 起
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="总成本"
          value={formatUsd(totals.cost_usd)}
          testId="admin-cost-total-cost"
        />
        <StatCard
          label="调用次数"
          testId="admin-cost-total-events"
          value={formatNumber(totals.events)}
          hint={
            formatNumber(totals.platform_events) +
            ' 平台 · ' +
            formatNumber(totals.byok_events) +
            ' BYOK'
          }
        />
        <StatCard
          label="tokens"
          value={formatNumber(totals.tokens)}
          testId="admin-cost-total-tokens"
        />
        <StatCard
          label="未计价条数"
          value={formatNumber(totals.unpriced_events)}
          testId="admin-cost-total-unpriced"
          badge={
            totals.unpriced_events > 0 ? (
              <ToneBadge tone="warning">未计价</ToneBadge>
            ) : null
          }
        />
      </div>

      <SourceSplit summary={data} />
      <DailyBars days={data.by_day} />
      <ModelTable models={data.by_model} />
      <TopAccounts accounts={data.top_accounts} />
    </div>
  );
}
