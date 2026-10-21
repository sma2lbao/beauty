import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { conversationsApi } from '@/lib/conversations-api';

import { ConsoleSidebar } from './sidebar';

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    page: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    messages: vi.fn(),
    postMessage: vi.fn(),
  },
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: { list: vi.fn(async () => []) },
  };
});

vi.mock('@/lib/account-api', () => ({
  accountApi: {
    organization: vi.fn(async () => ({ plan: '团队版' })),
  },
}));

vi.mock('@/lib/auth', () => ({
  accountLabel: vi.fn(async () => null),
}));

const conversation = {
  id: 'c1',
  title: '整理本周日程成周报',
  created_at: '2026-10-13T10:00:00+00:00',
  updated_at: '2026-10-13T10:05:00+00:00',
  archived_at: null,
};

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <ConsoleSidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(conversationsApi.page).mockResolvedValue({
    items: [conversation],
    next_cursor: null,
  });
  vi.mocked(conversationsApi.update).mockResolvedValue(conversation);
  vi.mocked(conversationsApi.remove).mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('ConsoleSidebar 会话管理', () => {
  it('搜索/归档切换传给分页接口', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.change(screen.getByLabelText('搜索会话'), {
      target: { value: '周报' },
    });
    await waitFor(() =>
      expect(conversationsApi.page).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: '周报', archived: false }),
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: '已归档' }));
    await waitFor(() =>
      expect(conversationsApi.page).toHaveBeenLastCalledWith(
        expect.objectContaining({ archived: true }),
      ),
    );
  });

  it('重命名会话调用 PATCH', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '重命名' }));
    const input = screen.getByLabelText('会话标题');
    fireEvent.change(input, { target: { value: '新的周报会话' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        title: '新的周报会话',
      }),
    );
  });

  it('底部通知入口能就地展开通知面板', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    expect(
      screen.getByRole('dialog', { name: '通知面板' }),
    ).toBeInTheDocument();
  });

  it('归档与删除调用对应接口', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '归档' }));
    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        archived: true,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() =>
      expect(conversationsApi.remove).toHaveBeenCalledWith('c1'),
    );
  });
});
