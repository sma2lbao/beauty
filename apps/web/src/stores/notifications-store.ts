/**
 * toast 浮层状态(zustand):右下角瞬时提示,到期自动消失,不持久化。
 *
 * P1-J 通知持久化后,**通知列表与未读**由服务端负责(见 lib/notifications-api
 * 与 features/notifications/use-notifications-model),本 store 不再持有通知数组
 * 与已读游标,只留 toast 池;push 仍接受 to 以便调用方少改(跳转由通知中心承担)。
 */
import { create } from 'zustand';

import type { NotificationTone } from '@/lib/notifications-api';

export type { NotificationTone };

export type Toast = {
  id: string;
  tone: NotificationTone;
  title: string;
  /** 可选正文(如 run 目标摘要)。 */
  description?: string;
  /** 创建时间(epoch ms)。 */
  issuedAt: number;
  /** 到期自动移除的时间戳(epoch ms)。 */
  expiresAt: number;
};

const DEFAULT_TTL_MS = 6000;

type NotificationsState = {
  toasts: Toast[];
  push: (input: {
    tone: NotificationTone;
    title: string;
    description?: string;
    /** 仅通知中心消费;toast 本身不可点击跳转。 */
    to?: string;
    /** 0 表示不弹 toast(当前会话内的变化只在通知中心里体现)。 */
    ttlMs?: number;
  }) => string;
  dismissToast: (id: string) => void;
  /** 测试与登出清理用。 */
  reset: () => void;
};

function nextId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

export const useNotificationsStore = create<NotificationsState>()((set) => ({
  toasts: [],
  push: (input) => {
    const issuedAt = Date.now();
    const id = nextId();
    if (input.ttlMs !== 0) {
      set((state) => ({
        toasts: [
          ...state.toasts,
          {
            id,
            tone: input.tone,
            title: input.title,
            description: input.description,
            issuedAt,
            expiresAt: issuedAt + (input.ttlMs ?? DEFAULT_TTL_MS),
          },
        ],
      }));
    }
    return id;
  },
  dismissToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter((toast) => toast.id !== id),
    }));
  },
  reset: () => {
    set({ toasts: [] });
  },
}));
