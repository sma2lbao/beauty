/**
 * 三个状态计数卡(待投递/已投递/已失败),数据未到位时显示占位符。
 */
import { Card, CardDescription, CardHeader, CardTitle } from '@beauty/design';

import type { OutboxStats } from '@/lib/outbox-api';

import { OUTBOX_STATUSES, STATUS_LABELS } from './status';

export function OutboxStatsCards({
  stats,
}: {
  stats: OutboxStats | undefined;
}) {
  return (
    <div className="mt-6 grid gap-4 sm:grid-cols-3">
      {OUTBOX_STATUSES.map((key) => (
        <Card key={key}>
          <CardHeader>
            <CardDescription>{STATUS_LABELS[key]}</CardDescription>
            <CardTitle className="text-3xl">{stats?.[key] ?? '—'}</CardTitle>
          </CardHeader>
        </Card>
      ))}
    </div>
  );
}
