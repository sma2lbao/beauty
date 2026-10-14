import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import type { OutboxEvent } from '@/lib/outbox-api';
import { outboxApi } from '@/lib/outbox-api';

import { OutboxPage } from './outbox';

vi.mock('@/lib/outbox-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/outbox-api')>('@/lib/outbox-api');
  return {
    ...actual,
    outboxApi: {
      stats: vi.fn(),
      events: vi.fn(),
      retry: vi.fn(),
      retryFailed: vi.fn(),
    },
  };
});

vi.mock('@/components/runs/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

const failedEvent: OutboxEvent = {
  id: 'e1',
  event_type: 'run.delivered.v1',
  status: 'failed',
  attempts: 5,
  payload: { run_id: 'run-1' },
  available_at: '2026-10-13T00:00:00+00:00',
  dispatched_at: null,
  last_error: 'HandlerError: KnowledgeIngestConsumer: UpstreamError 知识库不可达',
  created_at: '2026-10-13T00:00:00+00:00',
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OutboxPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(outboxApi.stats).mockResolvedValue({
    pending: 1,
    dispatched: 2,
    failed: 3,
  });
  vi.mocked(outboxApi.events).mockResolvedValue([failedEvent]);
});

describe('OutboxPage', () => {
  it('展示三个状态计数与事件明细', async () => {
    renderPage();

    // 「已失败」既是计数卡标题也是事件状态徽标 → 用 findAll
    expect((await screen.findAllByText('已失败')).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText('3')).toBeTruthy(); // failed 计数(等计数到位)
    expect(await screen.findByText('run.delivered.v1')).toBeTruthy();
    expect(screen.getByText('已尝试 5 次')).toBeTruthy();
    expect(screen.getByText(/KnowledgeIngestConsumer/)).toBeTruthy();
  });

  it('切换状态过滤会用对应过滤条件重新取数', async () => {
    renderPage();
    await screen.findByText('run.delivered.v1');

    fireEvent.change(screen.getByLabelText('状态'), {
      target: { value: 'failed' },
    });

    await waitFor(() => {
      expect(outboxApi.events).toHaveBeenLastCalledWith('failed');
    });
  });

  it('重放单条调用 retry 并刷新', async () => {
    vi.mocked(outboxApi.retry).mockResolvedValue({
      ...failedEvent,
      status: 'pending',
      attempts: 0,
    });
    renderPage();
    await screen.findByText('run.delivered.v1');

    fireEvent.click(screen.getByRole('button', { name: '重放' }));

    await waitFor(() => expect(outboxApi.retry).toHaveBeenCalledWith('e1'));
    await waitFor(() => expect(outboxApi.stats).toHaveBeenCalledTimes(2));
  });

  it('重放全部失败并展示条数', async () => {
    vi.mocked(outboxApi.retryFailed).mockResolvedValue({ requeued: 3 });
    renderPage();
    await screen.findByText('run.delivered.v1');

    fireEvent.click(screen.getByRole('button', { name: '重放全部失败' }));

    expect(await screen.findByText(/已重放 3 条/)).toBeTruthy();
  });

  it('没有 failed 时批量重放按钮禁用', async () => {
    vi.mocked(outboxApi.stats).mockResolvedValue({
      pending: 0,
      dispatched: 1,
      failed: 0,
    });
    renderPage();
    await screen.findByText('run.delivered.v1');

    expect(screen.getByRole('button', { name: '重放全部失败' })).toBeDisabled();
  });

  it('未启用 outbox 时给出明确说明而不是空列表', async () => {
    vi.mocked(outboxApi.stats).mockRejectedValue(
      new ApiError(409, 'outbox.disabled', 'outbox 未启用'),
    );
    vi.mocked(outboxApi.events).mockRejectedValue(
      new ApiError(409, 'outbox.disabled', 'outbox 未启用'),
    );

    renderPage();

    expect(await screen.findByText(/该服务未启用 outbox/)).toBeTruthy();
    expect(screen.getByText(/OUTBOX_ENABLED=false/)).toBeTruthy();
  });

  it('401 时提示先鉴权', async () => {
    vi.mocked(outboxApi.stats).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );

    renderPage();

    expect(
      await screen.findByText(/未登录或令牌失效:先在上方面板完成鉴权/),
    ).toBeTruthy();
  });
});
