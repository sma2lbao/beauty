/**
 * 本月配额条:已用 / 上限 token,以及剩余额度与超限警示。
 *
 * 配额落在账号行(users.quota),由服务端维护;未配置(monthly_tokens 为 null)
 * 表示不限制,这里如实显示而不是画一条 0% 的空进度条。
 */
import { Gauge } from 'lucide-react';

import { ConsolePanel, ToneBadge } from '@/components/console/ui';
import type { UsageQuota } from '@/lib/insights-api';

import { formatNumber } from './format-number';

export function QuotaPanel({ quota }: { quota: UsageQuota | undefined }) {
  const limit = quota?.monthly_tokens ?? null;
  const used = quota?.used_tokens ?? 0;
  const unlimited = quota !== undefined && limit === null;
  const ratio = limit && limit > 0 ? Math.min(used / limit, 1) : 0;
  const exhausted = limit !== null && used >= limit;
  const remaining = limit !== null ? Math.max(limit - used, 0) : 0;

  return (
    <ConsolePanel className="mt-6 p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-ha-text-2">
          <Gauge className="h-3.5 w-3.5 text-ha-muted" />
          本月配额
        </h2>
        {quota === undefined ? (
          <span className="font-mono text-[11px] text-ha-faint">读取中…</span>
        ) : unlimited ? (
          <ToneBadge tone="neutral">未配置配额 · 不限制</ToneBadge>
        ) : exhausted ? (
          <ToneBadge tone="danger">已超出配额</ToneBadge>
        ) : (
          <ToneBadge tone="success">
            剩余 {formatNumber(remaining)} tokens
          </ToneBadge>
        )}
      </div>
      {limit === null ? (
        <p className="mt-2 text-[12px] text-ha-faint">
          本月已用 {formatNumber(used)}{' '}
          tokens;账号未配置月度上限,发送不会被配额拦截。
        </p>
      ) : (
        <>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-ha-panel-2">
            <div
              className={
                exhausted ? 'h-full bg-ha-danger' : 'h-full bg-ha-accent'
              }
              style={{ width: `${Math.round(ratio * 100)}%` }}
            />
          </div>
          <p className="mt-2 flex items-center justify-between font-mono text-[11px] text-ha-faint">
            <span>
              {formatNumber(used)} / {formatNumber(limit)} tokens
            </span>
            <span>{Math.round(ratio * 100)}%</span>
          </p>
          {exhausted ? (
            <p className="mt-2 text-[12px] text-ha-danger">
              本月用量已达上限:新的消息与任务会被拒绝(429
              quota.monthly_tokens_exceeded),等待下月重置或提升配额。
            </p>
          ) : null}
        </>
      )}
    </ConsolePanel>
  );
}
