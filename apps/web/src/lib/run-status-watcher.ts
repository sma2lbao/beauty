/**
 * run 状态变化 → 站内通知的探测器。
 *
 * 两条数据源(P1-9b 起):
 * 1. **服务端推送**(首选):订阅 /api/v1/notifications/stream,后端 diff 后直接推变化;
 *    断线自动重连;连不上(老版本后端/代理不支持 SSE)不影响使用。
 * 2. **轮询兜底**:复用 sidebar 的 ['runs'] 5s 查询做 diff——SSE 不可用时通知不丢,
 *    同时在服务端推送进行中作为"补齐"通道(同一条通知按 runId+status 去重)。
 *
 * 首次见到某个 run 只记录基线不通知(避免刷新页面时把历史终态全弹一遍)。
 * 抽成纯函数 + React hook 两层:纯函数可直接单测。
 */
import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';

import type { Run, RunStatus } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import {
  streamNotifications,
  type RunStatusNotification,
} from '@/lib/notifications-api';
import {
  useNotificationsStore,
  type NotificationTone,
} from '@/stores/notifications-store';
import { useUiStore } from '@/stores/ui-store';

export type StatusChange = {
  runId: string;
  conversationId: string | null;
  from: RunStatus;
  to: RunStatus;
};

const NOTIFY_ON: Partial<
  Record<RunStatus, { tone: NotificationTone; title: string }>
> = {
  awaiting_approval: {
    tone: 'warning',
    title: '任务待审批',
  },
  awaiting_confirmation: {
    tone: 'warning',
    title: '任务待确认危险操作',
  },
  completed: { tone: 'success', title: '任务已完成' },
  failed: { tone: 'danger', title: '任务失败' },
  blocked: { tone: 'danger', title: '任务被阻塞' },
};

export function statusChanges(
  previous: Map<string, RunStatus>,
  runs: Run[],
): StatusChange[] {
  const changes: StatusChange[] = [];
  for (const run of runs) {
    const from = previous.get(run.id);
    if (from !== undefined && from !== run.status) {
      changes.push({
        runId: run.id,
        conversationId: run.conversation_id ?? null,
        from,
        to: run.status,
      });
    }
  }
  return changes;
}

export function pushRunNotifications(
  changes: StatusChange[],
  runsById: Map<string, Run>,
  push: (input: {
    tone: NotificationTone;
    title: string;
    description?: string;
    to?: string;
    ttlMs?: number;
  }) => void,
  currentConversationId: string | null,
): void {
  for (const change of changes) {
    const meta = NOTIFY_ON[change.to];
    if (!meta) continue;
    // 当前会话内的变化:页面本身就有状态条,只进列表不弹 toast,避免双打扰
    const inCurrentConversation =
      currentConversationId !== null &&
      change.conversationId === currentConversationId;
    push({
      tone: meta.tone,
      title: meta.title,
      description: runsById.get(change.runId)?.goal?.slice(0, 80),
      to: change.conversationId
        ? `/?conversation=${change.conversationId}`
        : '/runs',
      ttlMs: inCurrentConversation ? 0 : undefined,
    });
  }
}

/** 服务端推送载荷 → 通知;状态不属于"需要提醒"的直接忽略。 */
export function pushServerNotification(
  notification: RunStatusNotification,
  push: (input: {
    tone: NotificationTone;
    title: string;
    description?: string;
    to?: string;
    ttlMs?: number;
  }) => void,
  currentConversationId: string | null,
): boolean {
  const meta = NOTIFY_ON[notification.status as RunStatus];
  if (!meta) return false;
  const inCurrentConversation =
    currentConversationId !== null &&
    notification.conversation_id === currentConversationId;
  push({
    tone: meta.tone,
    title: meta.title,
    description: notification.goal?.slice(0, 80),
    to: notification.conversation_id
      ? `/?conversation=${notification.conversation_id}`
      : '/runs',
    ttlMs: inCurrentConversation ? 0 : undefined,
  });
  return true;
}

/**
 * 挂在 ConsoleLayout:
 * - 首选用服务端推送(SSE,断线指数退避重连);
 * - 同时保留 ['runs'] 轮询 diff 兜底(与 sidebar 共享同一 query key)。
 * 不能放在 sidebar 内,因为侧栏可被隐藏(隐藏时组件卸载,通知会断)。
 * 当前会话 id 从 ui-store 读取(chat 页写入),null 表示不在会话页。
 */
export function useRunStatusNotifications(): void {
  const push = useNotificationsStore((state) => state.push);
  const currentConversationId = useUiStore(
    (state) => state.currentConversationId,
  );
  const statusesRef = useRef<Map<string, RunStatus> | null>(null);
  // 去重:SSE 与轮询可能对同一次变化各推一次
  const notifiedRef = useRef<Set<string>>(new Set());

  const notifyOnce = (key: string, emit: () => void): void => {
    if (notifiedRef.current.has(key)) return;
    notifiedRef.current.add(key);
    emit();
  };

  // 与 sidebar 的 ['runs'] 同 key,共享缓存与轮询,不会多打请求
  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: runsApi.list,
    refetchInterval: 5000,
  });

  // 轮询兜底:SSE 正常时也在跑,但同一变化只通知一次
  useEffect(() => {
    const runs = runsQuery.data;
    if (!runs) return;
    const next = new Map(runs.map((run) => [run.id, run.status]));
    const previous = statusesRef.current;
    if (previous) {
      const changes = statusChanges(previous, runs);
      if (changes.length > 0) {
        const runsById = new Map(runs.map((run) => [run.id, run]));
        pushRunNotifications(changes, runsById, push, currentConversationId);
        for (const change of changes) {
          if (NOTIFY_ON[change.to]) {
            notifiedRef.current.add(`${change.runId}:${change.to}`);
          }
        }
      }
    }
    statusesRef.current = next;
  }, [runsQuery.data, push, currentConversationId]);

  // 服务端推送:断线指数退避重连;卸载即断开
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
            const key = `${notification.run_id}:${notification.status}`;
            notifyOnce(key, () => {
              pushServerNotification(notification, push, currentConversationId);
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
  }, [push, currentConversationId]);
}
