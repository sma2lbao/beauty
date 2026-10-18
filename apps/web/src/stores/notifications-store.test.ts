import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  unreadCount,
  useNotificationsStore,
} from './notifications-store';

describe('notificationsStore', () => {
  beforeEach(() => {
    useNotificationsStore.getState().reset();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('push 进通知列表并默认弹 toast,过期由视口负责', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    const id = useNotificationsStore.getState().push({
      tone: 'success',
      title: '任务已完成',
      description: '整理周报',
      to: '/runs',
    });
    const state = useNotificationsStore.getState();
    expect(state.notifications).toHaveLength(1);
    expect(state.notifications[0]).toMatchObject({
      id,
      tone: 'success',
      title: '任务已完成',
      to: '/runs',
      issuedAt: 1000,
    });
    expect(state.toasts).toHaveLength(1);
    expect(state.toasts[0].expiresAt).toBe(1000 + 6000);
  });

  it('ttlMs=0 只进列表不弹 toast', () => {
    useNotificationsStore.getState().push({
      tone: 'info',
      title: '当前会话的状态变化',
      ttlMs: 0,
    });
    const state = useNotificationsStore.getState();
    expect(state.notifications).toHaveLength(1);
    expect(state.toasts).toHaveLength(0);
  });

  it('markAllRead 推进已读游标,unreadCount 归零', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    useNotificationsStore.getState().push({ tone: 'info', title: 'a' });
    vi.spyOn(Date, 'now').mockReturnValue(2_000);
    useNotificationsStore.getState().push({ tone: 'danger', title: 'b' });
    const before = useNotificationsStore.getState();
    expect(unreadCount(before)).toBe(2);
    before.markAllRead();
    const after = useNotificationsStore.getState();
    expect(unreadCount(after)).toBe(0);
    expect(after.readCursor).toBe(2000);
  });

  it('dismissToast 只移除对应 toast', () => {
    useNotificationsStore.getState().push({ tone: 'info', title: 'a' });
    useNotificationsStore.getState().push({ tone: 'info', title: 'b' });
    const first = useNotificationsStore.getState().toasts[0];
    useNotificationsStore.getState().dismissToast(first.id);
    const toasts = useNotificationsStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].id).not.toBe(first.id);
  });

  it('通知列表超过 50 条时截断最旧的', () => {
    let now = 0;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    for (let i = 0; i < 55; i += 1) {
      now = i + 1;
      useNotificationsStore.getState().push({
        tone: 'info',
        title: `n${i}`,
        ttlMs: 0,
      });
    }
    const notifications = useNotificationsStore.getState().notifications;
    expect(notifications).toHaveLength(50);
    expect(notifications[0].title).toBe('n54');
    expect(notifications[49].title).toBe('n5');
  });
});
