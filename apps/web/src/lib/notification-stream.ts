/**
 * 账号级通知的实时桥:订阅 /api/v1/notifications/stream,把推送转成两步 ——
 * 1. 失效 ['notifications'] 缓存(通知中心/未读数重新拉取服务端状态);
 * 2. 不在当前会话页时弹 toast(会话内的变化页面自带状态条,只留在通知中心)。
 *
 * P1-J 通知持久化后,前端不再从 ['runs'] 轮询 diff 出通知:列表与未读在服务端,
 * 轮询兜底移到 use-notifications-model 的 30s refetchInterval。
 * 挂在 ConsoleLayout:不能放在 sidebar 内,因为侧栏可被隐藏(隐藏即卸载会断流)。
 */
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { streamNotifications } from '@/lib/notifications-api';
import { queryKeys } from '@/lib/query-keys';
import { useNotificationsStore } from '@/stores/notifications-store';
import { useUiStore } from '@/stores/ui-store';

export function useNotificationStream(): void {
  const push = useNotificationsStore((state) => state.push);
  const currentConversationId = useUiStore(
    (state) => state.currentConversationId,
  );
  const queryClient = useQueryClient();
  // 重连时服务端可能从游标重放同一行:toast 按通知 id 去重。
  const toastedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    function scheduleRetry() {
      if (cancelled) return;
      const delay = Math.min(1000 * 2 ** attempt, 30_000);
      attempt += 1;
      retryTimer = setTimeout(() => void connect(), delay);
    }

    async function connect(): Promise<void> {
      if (cancelled) return;
      controller = new AbortController();
      try {
        await streamNotifications({
          signal: controller.signal,
          onOpen: () => {
            attempt = 0;
          },
          onNotification: (notification) => {
            void queryClient.invalidateQueries({
              queryKey: queryKeys.notifications,
            });
            const inCurrentConversation =
              currentConversationId !== null &&
              notification.conversation_id === currentConversationId;
            if (
              inCurrentConversation ||
              toastedRef.current.has(notification.id)
            )
              return;
            toastedRef.current.add(notification.id);
            push({
              tone: notification.tone,
              title: notification.title,
              description: notification.body || undefined,
              to: notification.to_path ?? undefined,
            });
          },
        });
        // 服务端正常结束:重连
        scheduleRetry();
      } catch (error) {
        if (cancelled || (error as Error)?.name === 'AbortError') return;
        scheduleRetry();
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
  }, [push, currentConversationId, queryClient]);
}
