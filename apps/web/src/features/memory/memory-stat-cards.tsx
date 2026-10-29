/**
 * 统计卡:记忆总数 / 有效 / 置顶 / 已过期。
 */
import { Card, CardDescription, CardHeader, CardTitle } from '@beauty/design';

import type { MemoryStats } from '@/lib/memory-api';

import { STAT_ITEMS } from './display';

export function MemoryStatCards({ stats }: { stats?: MemoryStats }) {
  return (
    <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {STAT_ITEMS.map((item) => (
        <Card key={item.key}>
          <CardHeader>
            <CardDescription>{item.label}</CardDescription>
            <CardTitle className="text-3xl">
              {stats?.[item.key] ?? '—'}
            </CardTitle>
            <CardDescription>{item.hint}</CardDescription>
          </CardHeader>
        </Card>
      ))}
    </div>
  );
}
