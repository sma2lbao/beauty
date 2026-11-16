import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useNotificationStream } from './notification-stream';
import {
  streamNotifications,
  type AppNotification,
} from '@/lib/notifications-api';
import { queryKeys } from '@/lib/query-keys';
import { useNotificationsStore } from '@/stores/notifications-store';
import { useUiStore } from '@/stores/ui-store';

vi.mock('@/lib/notifications-api', async () => {
  const actual = await vi.importActual<
    typeof import('@/lib/notifications-api')
  >('@/lib/notifications-api');
  return { ...actual, streamNotifications: vi.fn() };
});

const stream = vi.mocked(streamNotifications);

function notification(
  overrides: Partial<AppNotification> = {},
): AppNotification {
  return {
    id: 'n1',
    kind: 'run.status',
    title: '任务已完成',
    body: '整理周报',
    tone: 'success',
    to_path: '/?conversation=c1',
    run_id: 'r1',
    conversation_id: 'c1',
    payload: {},
    created_at: '2026-11-16T12:00:00+00:00',
    read_at: null,
    ...overrides,
  };
}

/** 订阅被挂住(流不结束),测试手动驱动 onNotification。 */
let handlers: Parameters<typeof streamNotifications>[0] | null = null;

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const rendered = renderHook(() => useNotificationStream(), { wrapper });
  return { ...rendered, invalidate };
}

describe('useNotificationStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    handlers = null;
    useNotificationsStore.getState().reset();
    useUiStore.getState().setCurrentConversation(null);
    stream.mockImplementation((next) => {
      handlers = next;
      return new Promise<void>(() => {});
    });
  });

  it('推送到达时失效通知缓存,非当前会话再弹 toast', async () => {
    const { invalidate } = setup();
    await waitFor(() => expect(handlers).not.toBeNull());

    act(() => handlers!.onNotification(notification()));

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.notifications,
    });
    const { toasts } = useNotificationsStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatchObject({
      tone: 'success',
      title: '任务已完成',
      description: '整理周报',
    });
  });

  it('当前会话内的变化只失效缓存,不弹 toast', async () => {
    useUiStore.getState().setCurrentConversation('c1');
    const { invalidate } = setup();
    await waitFor(() => expect(handlers).not.toBeNull());

    act(() => handlers!.onNotification(notification()));

    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(useNotificationsStore.getState().toasts).toHaveLength(0);
  });

  it('重放同一行时 toast 按通知 id 去重', async () => {
    setup();
    await waitFor(() => expect(handlers).not.toBeNull());

    act(() => {
      handlers!.onNotification(notification());
      handlers!.onNotification(notification());
    });

    expect(useNotificationsStore.getState().toasts).toHaveLength(1);
  });
});
