/**
 * 事件卡片:状态过滤、批量重放、事件明细与单条重放。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Select,
  Spin,
  Tag,
} from '@beauty/design';

import type { OutboxStatus } from '@/lib/outbox-api';
import { formatDateTime } from '@/lib/presentation';

import { STATUS_COLORS, STATUS_LABELS } from './status';
import type { OutboxModel } from './use-outbox-model';

export function OutboxEventsCard({ model }: { model: OutboxModel }) {
  const {
    status,
    setStatus,
    stats,
    events,
    eventsLoading,
    retryMutation,
    retryFailedMutation,
  } = model;

  return (
    <Card className="mt-6">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>事件</CardTitle>
            <CardDescription>
              {events.length > 0
                ? `共 ${events.length} 条(最新在前)`
                : '没有符合条件的事件'}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <label
              className="text-sm text-muted-foreground"
              htmlFor="outbox-status"
            >
              状态
            </label>
            <Select
              id="outbox-status"
              className="w-32"
              value={status}
              onChange={(event) =>
                setStatus(event.target.value as OutboxStatus | 'all')
              }
            >
              <option value="all">全部</option>
              <option value="pending">待投递</option>
              <option value="dispatched">已投递</option>
              <option value="failed">已失败</option>
            </Select>
            <Button
              variant="outline"
              size="sm"
              onClick={() => retryFailedMutation.mutate()}
              disabled={
                retryFailedMutation.isPending || (stats?.failed ?? 0) === 0
              }
            >
              {retryFailedMutation.isPending ? '重放中…' : '重放全部失败'}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {retryFailedMutation.isSuccess ? (
          <p className="mb-3 text-sm text-muted-foreground">
            已重放 {retryFailedMutation.data.requeued} 条,等待下一轮投递。
          </p>
        ) : null}

        {eventsLoading ? (
          <Spin label="加载中…" />
        ) : events.length === 0 ? (
          <Empty description="暂无事件:跑到交付或交付物入库后这里会有记录。" />
        ) : (
          <ul className="divide-y">
            {events.map((event) => (
              <li key={event.id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Tag color={STATUS_COLORS[event.status]}>
                        {STATUS_LABELS[event.status]}
                      </Tag>
                      <span className="font-mono text-sm">
                        {event.event_type}
                      </span>
                      {event.attempts > 0 ? (
                        <span className="text-xs text-muted-foreground">
                          已尝试 {event.attempts} 次
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDateTime(event.created_at)}
                      {event.dispatched_at
                        ? ` · 投递于 ${formatDateTime(event.dispatched_at)}`
                        : ''}
                    </p>
                    {event.last_error ? (
                      <p className="mt-1 break-all text-xs text-destructive">
                        {event.last_error}
                      </p>
                    ) : null}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => retryMutation.mutate(event.id)}
                    disabled={
                      retryMutation.isPending || event.status === 'dispatched'
                    }
                  >
                    重放
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {retryMutation.isError ? (
          <p className="mt-3 text-sm text-destructive">
            {(retryMutation.error as Error).message}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
