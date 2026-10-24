import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  Alert,
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

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/components/runs/auth-bar';
import { ApiError, isUnauthorized } from '@/lib/http';
import type { OutboxStatus } from '@/lib/outbox-api';
import { outboxApi } from '@/lib/outbox-api';
import { formatDateTime } from '@/lib/presentation';

const STATUS_LABELS: Record<OutboxStatus, string> = {
  pending: '待投递',
  dispatched: '已投递',
  failed: '已失败',
};

const STATUS_COLORS: Record<OutboxStatus, 'warning' | 'success' | 'danger'> = {
  pending: 'warning',
  dispatched: 'success',
  failed: 'danger',
};

const EVENTS_KEY = ['outbox-events'];
const STATS_KEY = ['outbox-stats'];

/** 事件队列运维页:看 outbox 状态、人工重放失败事件(§6.3)。 */
export function OutboxPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<OutboxStatus | 'all'>('all');

  const statsQuery = useQuery({ queryKey: STATS_KEY, queryFn: outboxApi.stats });
  const eventsQuery = useQuery({
    queryKey: [...EVENTS_KEY, status],
    queryFn: () => outboxApi.events(status === 'all' ? undefined : status),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: STATS_KEY }),
      queryClient.invalidateQueries({ queryKey: EVENTS_KEY }),
    ]);
  };

  const retryMutation = useMutation({
    mutationFn: (eventId: string) => outboxApi.retry(eventId),
    onSuccess: refresh,
  });
  const retryFailedMutation = useMutation({
    mutationFn: () => outboxApi.retryFailed(),
    onSuccess: refresh,
  });

  const events = eventsQuery.data ?? [];
  const stats = statsQuery.data;
  const errors = [
    statsQuery.error,
    eventsQuery.error,
    retryMutation.error,
    retryFailedMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const disabled = errors.some(
    (error) => error instanceof ApiError && error.code === 'outbox.disabled',
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="事件队列"
        subtitle="发件箱(outbox):同事务落库、最低一次投递、退避重试,失败可人工重放"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void refresh()} />

      {unauthorized ? (
        <Alert variant="danger" className="mt-4">
          未登录或令牌失效:先在上方面板完成鉴权,再运维事件队列。
        </Alert>
      ) : null}

      {disabled ? (
        <Alert variant="neutral" className="mt-4 border-dashed">
          该服务未启用 outbox(<span className="font-mono">
            OUTBOX_ENABLED=false
          </span>
          ):没有事件流可运维。启用后重启服务即可。
        </Alert>
      ) : null}

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {(['pending', 'dispatched', 'failed'] as OutboxStatus[]).map((key) => (
          <Card key={key}>
            <CardHeader>
              <CardDescription>{STATUS_LABELS[key]}</CardDescription>
              <CardTitle className="text-3xl">{stats?.[key] ?? '—'}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

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
              <label className="text-sm text-muted-foreground" htmlFor="outbox-status">
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

          {eventsQuery.isLoading ? (
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
                        <span className="font-mono text-sm">{event.event_type}</span>
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
      </div>
    </div>
  );
}
