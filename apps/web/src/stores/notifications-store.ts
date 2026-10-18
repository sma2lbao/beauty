/**
 * 站内通知中心(zustand):
 * - toast 池:瞬时的右下角浮层,自动过期;store 持久化不含 toast;
 * - 通知列表:最近的 run 状态变化等事件,带未读计数;已读进度用游标(最新已读通知的
 *   issuedAt)持久化,避免整表落 localStorage。
 *
 * 通知由前端从既有轮询(runsQuery 5s)diff 产生,后端零改动;
 * 将来接服务端推送时,只需调用 push(),消费方不变。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type NotificationTone = 'info' | 'success' | 'warning' | 'danger';

export type AppNotification = {
  id: string;
  tone: NotificationTone;
  title: string;
  /** 可选正文(如 run 目标摘要)。 */
  description?: string;
  /** 点击跳转地址(相对路径,如 /runs 或 /?conversation=<id>)。 */
  to?: string;
  /** 创建时间(epoch ms)。 */
  issuedAt: number;
};

type Toast = AppNotification & {
  /** 到期自动移除的时间戳(epoch ms)。 */
  expiresAt: number;
};

const MAX_NOTIFICATIONS = 50;
const DEFAULT_TTL_MS = 6000;

type NotificationsState = {
  notifications: AppNotification[];
  toasts: Toast[];
  /** 已读进度:issuedAt 不晚于该值的通知都视为已读。 */
  readCursor: number;
  push: (input: {
    tone: NotificationTone;
    title: string;
    description?: string;
    to?: string;
    /** 0 表示只进通知列表,不弹 toast。 */
    ttlMs?: number;
  }) => string;
  dismissToast: (id: string) => void;
  markAllRead: () => void;
  /** 测试与登出清理用。 */
  reset: () => void;
};

function nextId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

export const useNotificationsStore = create<NotificationsState>()(
  persist(
    (set, get) => ({
      notifications: [],
      toasts: [],
      readCursor: 0,
      push: (input) => {
        const issuedAt = Date.now();
        const id = nextId();
        const notification: AppNotification = {
          id,
          tone: input.tone,
          title: input.title,
          description: input.description,
          to: input.to,
          issuedAt,
        };
        set((state) => ({
          notifications: [notification, ...state.notifications].slice(
            0,
            MAX_NOTIFICATIONS,
          ),
          toasts:
            input.ttlMs === 0
              ? state.toasts
              : [
                  ...state.toasts,
                  {
                    ...notification,
                    expiresAt: issuedAt + (input.ttlMs ?? DEFAULT_TTL_MS),
                  },
                ],
        }));
        return id;
      },
      dismissToast: (id) => {
        set((state) => ({
          toasts: state.toasts.filter((toast) => toast.id !== id),
        }));
      },
      markAllRead: () => {
        const latest = get().notifications[0];
        set({ readCursor: latest ? latest.issuedAt : Date.now() });
      },
      reset: () => {
        set({ notifications: [], toasts: [], readCursor: 0 });
      },
    }),
    {
      name: 'beauty-notifications',
      partialize: (state) => ({
        // toast 是瞬态;只持久化通知列表与已读游标
        notifications: state.notifications,
        readCursor: state.readCursor,
      }),
    },
  ),
);

export function unreadCount(state: {
  notifications: AppNotification[];
  readCursor: number;
}): number {
  return state.notifications.filter((n) => n.issuedAt > state.readCursor)
    .length;
}
