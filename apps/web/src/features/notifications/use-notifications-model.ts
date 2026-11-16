/**
 * 通知中心的数据与交互模型(P1-J 通知持久化):列表、未读数、已读回写都走服务端。
 *
 * 推送通道(SSE)只负责失效缓存,见 lib/notification-stream;这里保留 30s
 * 轮询作为推送不可用(老后端/代理不支持流)时的兜底,未读数不会长期滞后。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { notificationsApi } from '@/lib/notifications-api';
import { queryKeys } from '@/lib/query-keys';

const LIST_LIMIT = 20;
const REFETCH_INTERVAL_MS = 30_000;

export function useNotificationsModel() {
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: queryKeys.notifications,
    queryFn: () => notificationsApi.list({ limit: LIST_LIMIT }),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.notifications });

  // 已读是服务端状态:写成功后整域失效,未读数与行内 read_at 一起刷新。
  const markReadMutation = useMutation({
    mutationFn: (id: string) => notificationsApi.markRead(id),
    onSuccess: invalidate,
  });
  const markAllReadMutation = useMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: invalidate,
  });

  const data = listQuery.data;

  return {
    items: data?.items ?? [],
    unreadCount: data?.unread_count ?? 0,
    isLoading: listQuery.isLoading,
    error: listQuery.error,
    markRead: markReadMutation.mutate,
    markAllRead: markAllReadMutation.mutate,
  };
}

export type NotificationsModel = ReturnType<typeof useNotificationsModel>;
