import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { AuthBar } from '@/components/runs/auth-bar';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { ApiError, isUnauthorized } from '@/lib/http';
import type { OutboxStatus } from '@/lib/outbox-api';
import { outboxApi } from '@/lib/outbox-api';
import { cn } from '@/lib/utils';

const STATUS_LABELS: Record<OutboxStatus, string> = {
  pending: '待投递',
  dispatched: '已投递',
  failed: '已失败',
};

const STATUS_STYLES: Record<OutboxStatus, string> = {
  pending: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  dispatched: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  failed: 'bg-destructive/15 text-destructive',
};

const EVENTS_KEY = ['outbox-events'];
const STATS_KEY = ['outbox-stats'];

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

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
    <section className="mx-auto max-w-6xl px-6 py-12">
      <h1 className="font-display text-3xl">事件队列</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        跨服务事件的发件箱(outbox):事件与业务状态同事务落库,再由后台最低一次投递。
        投递失败的会按退避重试,超限落 <span className="font-mono">failed</span>
        ,可以在这里人工重放。
      </p>

      <div className="mt-6">
        <AuthBar onChange={() => void refresh()} />
      </div>

      {unauthorized ? (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
          未登录或令牌失效:先在上方面板完成鉴权,再运维事件队列。
        </p>
      ) : null}

      {disabled ? (
        <p className="mt-4 rounded-md border border-dashed px-3 py-2 text-sm">
          该服务未启用 outbox(<span className="font-mono">
            OUTBOX_ENABLED=false
          </span>
          ):没有事件流可运维。启用后重启服务即可。
        </p>
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
              <select
                id="outbox-status"
                className="rounded-md border bg-transparent px-2 py-1 text-sm"
                value={status}
                onChange={(event) =>
                  setStatus(event.target.value as OutboxStatus | 'all')
                }
              >
                <option value="all">全部</option>
                <option value="pending">待投递</option>
                <option value="dispatched">已投递</option>
                <option value="failed">已失败</option>
              </select>
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
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : events.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              暂无事件:跑到交付或交付物入库后这里会有记录。
            </p>
          ) : (
            <ul className="divide-y">
              {events.map((event) => (
                <li key={event.id} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={cn(
                            'rounded px-2 py-0.5 text-xs',
                            STATUS_STYLES[event.status],
                          )}
                        >
                          {STATUS_LABELS[event.status]}
                        </span>
                        <span className="font-mono text-sm">{event.event_type}</span>
                        {event.attempts > 0 ? (
                          <span className="text-xs text-muted-foreground">
                            已尝试 {event.attempts} 次
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatTime(event.created_at)}
                        {event.dispatched_at
                          ? ` · 投递于 ${formatTime(event.dispatched_at)}`
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
    </section>
  );
}
