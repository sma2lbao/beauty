import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NotificationCenter } from './notification-center';
import { useNotificationsStore } from '@/stores/notifications-store';

describe('NotificationCenter', () => {
  beforeEach(() => {
    useNotificationsStore.getState().reset();
    localStorage.clear();
  });

  it('无通知时铃铛无角标,面板提示空态', () => {
    render(
      <MemoryRouter>
        <NotificationCenter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    expect(screen.getByText('还没有通知。')).toBeInTheDocument();
  });

  it('未读数展示角标,点开面板可见通知项,全部已读清零角标', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    const store = useNotificationsStore.getState();
    store.push({ tone: 'success', title: '任务已完成', ttlMs: 0 });
    vi.spyOn(Date, 'now').mockReturnValue(2_000);
    store.push({
      tone: 'danger',
      title: '任务失败',
      description: '整理周报',
      ttlMs: 0,
    });
    render(
      <MemoryRouter>
        <NotificationCenter />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole('button', { name: '通知(2 条未读)' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '通知(2 条未读)' }));
    expect(screen.getByText('任务已完成')).toBeInTheDocument();
    expect(screen.getByText('任务失败')).toBeInTheDocument();
    expect(screen.getByText('整理周报')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '全部已读' }));
    expect(screen.getByRole('button', { name: '通知' })).toBeInTheDocument();
  });

  it('点击通知项关闭面板并标记已读', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    useNotificationsStore
      .getState()
      .push({ tone: 'success', title: '任务已完成', to: '/runs', ttlMs: 0 });
    render(
      <MemoryRouter>
        <NotificationCenter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: '通知(1 条未读)' }));
    fireEvent.click(screen.getByRole('button', { name: /任务已完成/ }));
    expect(screen.getByRole('button', { name: '通知' })).toBeInTheDocument();
    expect(
      screen.queryByRole('dialog', { name: '通知面板' }),
    ).not.toBeInTheDocument();
    expect(useNotificationsStore.getState().readCursor).toBe(1_000);
  });

  it('bar 形态(侧栏底部整行入口)同样能展开面板', () => {
    useNotificationsStore
      .getState()
      .push({ tone: 'warning', title: '侧栏通知', ttlMs: 0 });
    render(
      <MemoryRouter>
        <NotificationCenter variant="bar" />
      </MemoryRouter>,
    );
    expect(screen.getByText('通知')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '通知(1 条未读)' }));
    expect(
      screen.getByRole('dialog', { name: '通知面板' }),
    ).toBeInTheDocument();
    expect(screen.getByText('侧栏通知')).toBeInTheDocument();
  });

  it('Escape 关闭面板并把焦点还给铃铛', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    useNotificationsStore
      .getState()
      .push({ tone: 'info', title: 'x', ttlMs: 0 });
    render(
      <MemoryRouter>
        <NotificationCenter />
      </MemoryRouter>,
    );
    const bell = screen.getByRole('button', { name: '通知(1 条未读)' });
    fireEvent.click(bell);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(
      screen.queryByRole('dialog', { name: '通知面板' }),
    ).not.toBeInTheDocument();
    expect(bell).toHaveFocus();
  });
});
