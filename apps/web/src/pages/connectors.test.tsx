import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { connectorApi } from '@/lib/connector-api';

import { ConnectorsPage } from './connectors';

vi.mock('@/lib/connector-api', () => ({
  connectorApi: {
    list: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
    emails: vi.fn(),
    sendEmail: vi.fn(),
  },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectorsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ConnectorsPage', () => {
  it('空态提示添加连接', async () => {
    vi.mocked(connectorApi.list).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/还没有连接/)).toBeInTheDocument();
  });

  it('展示连接与最近邮件', async () => {
    vi.mocked(connectorApi.list).mockResolvedValue([
      {
        id: 'c1',
        provider: 'imap',
        account: 'me@example.com',
        display_name: '工作邮箱',
        status: 'active',
        last_checked_at: null,
        created_at: '2026-10-18T00:00:00+00:00',
      },
    ]);
    vi.mocked(connectorApi.emails).mockResolvedValue([
      {
        subject: '本周周报',
        sender: 'boss@example.com',
        sent_at: '2026-10-18',
        snippet: '请汇总',
        id: 'm1',
        thread_id: 't1',
        has_attachments: true,
      },
    ]);
    renderPage();
    expect((await screen.findAllByText('工作邮箱')).length).toBeGreaterThan(0);
    expect(await screen.findByText('本周周报')).toBeInTheDocument();
  });
});
