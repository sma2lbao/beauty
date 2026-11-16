/**
 * 通知数据层(P1-J 通知持久化):agent-orchestrator 的 /api/v1/notifications。
 *
 * 通知是**服务端的持久化行**(关掉页面也能留下未读),前端不再自行 diff run
 * 状态;本模块只负责读列表/写已读,以及订阅 SSE 做实时刷新。
 *
 * SSE 复用 @/lib/sse 基座(runs-api 同构):手写 fetch + ReadableStream 而不用
 * EventSource —— 需要带 Bearer 头与 credentials,EventSource 做不到;注释行
 * (心跳)由基座跳过。连接失败抛 ApiError,由调用方决定降级。
 */
import { ApiError, createHttpClient } from '@/lib/http';
import { connectSse } from '@/lib/sse';
import { runsBase as BASE } from '@/config/env';

/** 通知色调:与 run 状态的语义色一致,落库时由服务端决定。 */
export type NotificationTone = 'info' | 'success' | 'warning' | 'danger';

/** 服务端通知行(`NotificationRead` 的投影)。 */
export type AppNotification = {
  id: string;
  kind: string;
  title: string;
  body: string;
  tone: NotificationTone;
  /** 点击跳转的相对路径(如 /runs 或 /?conversation=<id>),可为空。 */
  to_path: string | null;
  run_id: string | null;
  conversation_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  /** 已读时间;null 表示未读。 */
  read_at: string | null;
};

export type NotificationList = {
  items: AppNotification[];
  unread_count: number;
  /** 下一页游标(传给 list 的 before);没有更多时为 null。 */
  next_cursor: string | null;
};

const client = createHttpClient(BASE + '/api/v1');
const NEWLINE = String.fromCharCode(10);

export const notificationsApi = {
  list: ({
    limit = 20,
    before = null,
  }: { limit?: number; before?: string | null } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (before) params.set('before', before);
    return client.request<NotificationList>(
      '/notifications?' + params.toString(),
    );
  },
  markRead: (id: string) =>
    client.request<AppNotification>(
      '/notifications/' + encodeURIComponent(id) + '/read',
      { method: 'POST' },
    ),
  markAllRead: () =>
    client.request<{ updated: number }>('/notifications/read-all', {
      method: 'POST',
    }),
};

function parseData(frame: string): AppNotification | null {
  for (const line of frame.split(NEWLINE)) {
    if (!line.startsWith('data: ')) continue;
    try {
      return JSON.parse(line.slice(6)) as AppNotification;
    } catch {
      return null;
    }
  }
  return null;
}

/** 订阅账号级通知;连接失败抛 ApiError,由调用方决定降级。 */
export async function streamNotifications(handlers: {
  onOpen?: () => void;
  onNotification: (notification: AppNotification) => void;
  signal?: AbortSignal;
}): Promise<void> {
  await connectSse(BASE + '/api/v1/notifications/stream', {
    signal: handlers.signal,
    onOpen: handlers.onOpen,
    mapError: (response) =>
      new ApiError(
        response.status,
        'notifications.stream_failed',
        '通知流连接失败(' + response.status + ')',
      ),
    onFrame: (frame) => {
      const notification = parseData(frame.raw);
      if (notification) {
        handlers.onNotification(notification);
      }
    },
  });
}
