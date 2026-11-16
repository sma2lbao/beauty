import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useNotificationsStore } from './notifications-store';

describe('notificationsStore', () => {
  beforeEach(() => {
    useNotificationsStore.getState().reset();
    vi.restoreAllMocks();
  });

  it('push 弹 toast 并默认 6s 过期', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    const id = useNotificationsStore.getState().push({
      tone: 'success',
      title: '任务已完成',
      description: '整理周报',
      to: '/runs',
    });
    const { toasts } = useNotificationsStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatchObject({
      id,
      tone: 'success',
      title: '任务已完成',
      description: '整理周报',
      issuedAt: 1000,
      expiresAt: 1000 + 6000,
    });
  });

  it('ttlMs=0 只标记不弹 toast(当前会话内的变化)', () => {
    const id = useNotificationsStore
      .getState()
      .push({ tone: 'info', title: '当前会话的状态变化', ttlMs: 0 });
    expect(id).toBeTruthy();
    expect(useNotificationsStore.getState().toasts).toHaveLength(0);
  });

  it('dismissToast 只移除对应 toast,reset 清空全部', () => {
    useNotificationsStore.getState().push({ tone: 'info', title: 'a' });
    useNotificationsStore.getState().push({ tone: 'info', title: 'b' });
    const first = useNotificationsStore.getState().toasts[0];
    useNotificationsStore.getState().dismissToast(first.id);
    const toasts = useNotificationsStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].id).not.toBe(first.id);

    useNotificationsStore.getState().reset();
    expect(useNotificationsStore.getState().toasts).toHaveLength(0);
  });
});
