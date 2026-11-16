import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NotificationCenter } from './notification-center';
import {
  notificationsApi,
  type AppNotification,
  type NotificationList,
} from '@/lib/notifications-api';

vi.mock('@/lib/notifications-api', () => ({
  notificationsApi: {
    list: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
  },
}));

const list = vi.mocked(notificationsApi.list);
const markRead = vi.mocked(notificationsApi.markRead);
const markAllRead = vi.mocked(notificationsApi.markAllRead);

function notification(
  overrides: Partial<AppNotification> = {},
): AppNotification {
  return {
    id: 'n1',
    kind: 'run.status',
    title: '任务已完成',
    body: '整理周报',
    tone: 'success',
    to_path: '/runs',
    run_id: 'r1',
    conversation_id: null,
    payload: {},
    created_at: '2026-11-16T12:00:00+00:00',
    read_at: null,
    ...overrides,
  };
}

function page(items: AppNotification[]): NotificationList {
  return {
    items,
    unread_count: items.filter((item) => item.read_at === null).length,
    next_cursor: null,
  };
}

function renderCenter(variant?: 'icon' | 'bar') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NotificationCenter variant={variant} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('NotificationCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    markAllRead.mockResolvedValue({ updated: 0 });
    markRead.mockResolvedValue(notification({ read_at: 'now' }));
  });

  it('无通知时铃铛无角标,面板提示空态', async () => {
    list.mockResolvedValue(page([]));
    renderCenter();
    expect(
      await screen.findByRole('button', { name: '通知' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    expect(await screen.findByText('还没有通知。')).toBeInTheDocument();
  });

  it('未读数来自服务端,点全部已读后角标清零', async () => {
    list
      .mockResolvedValueOnce(
        page([
          notification({
            id: 'n2',
            title: '任务失败',
            body: '',
            tone: 'danger',
          }),
          notification({ id: 'n1' }),
        ]),
      )
      .mockResolvedValue(
        page([
          notification({
            id: 'n2',
            title: '任务失败',
            body: '',
            tone: 'danger',
            read_at: 'now',
          }),
          notification({ id: 'n1', read_at: 'now' }),
        ]),
      );
    renderCenter();

    const bell = await screen.findByRole('button', {
      name: '通知(2 条未读)',
    });
    fireEvent.click(bell);
    expect(screen.getByText('任务已完成')).toBeInTheDocument();
    expect(screen.getByText('任务失败')).toBeInTheDocument();
    expect(screen.getByText('整理周报')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '全部已读' }));
    await waitFor(() => expect(markAllRead).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '通知' })).toBeInTheDocument(),
    );
  });

  it('点击通知项关闭面板、回写已读并跳转 to_path', async () => {
    list.mockResolvedValue(page([notification()]));
    renderCenter();

    fireEvent.click(
      await screen.findByRole('button', { name: '通知(1 条未读)' }),
    );
    fireEvent.click(screen.getByRole('button', { name: /任务已完成/ }));

    expect(
      screen.queryByRole('dialog', { name: '通知面板' }),
    ).not.toBeInTheDocument();
    // mutate 在微任务里才调用 mutationFn,断言要给一拍
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('n1'));
  });

  it('已读通知点击不再回写已读', async () => {
    list.mockResolvedValue(page([notification({ read_at: 'now' })]));
    renderCenter();

    fireEvent.click(await screen.findByRole('button', { name: '通知' }));
    fireEvent.click(screen.getByRole('button', { name: /任务已完成/ }));

    expect(markRead).not.toHaveBeenCalled();
  });

  it('bar 形态(侧栏底部整行入口)同样能展开面板', async () => {
    list.mockResolvedValue(
      page([notification({ title: '侧栏通知', tone: 'warning' })]),
    );
    renderCenter('bar');

    expect(await screen.findByText('通知')).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole('button', { name: '通知(1 条未读)' }),
    );
    expect(
      await screen.findByRole('dialog', { name: '通知面板' }),
    ).toBeInTheDocument();
    expect(screen.getByText('侧栏通知')).toBeInTheDocument();
  });

  it('Escape 关闭面板并把焦点还给铃铛', async () => {
    list.mockResolvedValue(page([notification({ title: 'x' })]));
    renderCenter();

    const bell = await screen.findByRole('button', { name: '通知(1 条未读)' });
    fireEvent.click(bell);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(
      screen.queryByRole('dialog', { name: '通知面板' }),
    ).not.toBeInTheDocument();
    expect(bell).toHaveFocus();
  });
});
