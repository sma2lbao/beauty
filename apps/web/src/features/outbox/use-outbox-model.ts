/**
 * 事件队列页的数据与交互模型:统计/事件查询、状态过滤与人工重放。
 * 「未启用 outbox」按 409 错误码判定,展示层只消费布尔量。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError, isUnauthorized } from '@/lib/http';
import type { OutboxStatus } from '@/lib/outbox-api';
import { outboxApi } from '@/lib/outbox-api';
import { queryKeys } from '@/lib/query-keys';

export function useOutboxModel() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<OutboxStatus | 'all'>('all');

  const statsQuery = useQuery({
    queryKey: queryKeys.outboxStats,
    queryFn: outboxApi.stats,
  });
  const eventsQuery = useQuery({
    queryKey: queryKeys.outboxEvents(status),
    queryFn: () => outboxApi.events(status === 'all' ? undefined : status),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.outboxStats }),
      queryClient.invalidateQueries({ queryKey: queryKeys.outboxEvents() }),
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

  return {
    status,
    setStatus,
    stats,
    events,
    eventsLoading: eventsQuery.isLoading,
    unauthorized,
    disabled,
    retryMutation,
    retryFailedMutation,
    refresh,
  };
}

export type OutboxModel = ReturnType<typeof useOutboxModel>;
