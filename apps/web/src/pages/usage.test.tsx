import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { insightsApi } from '@/lib/insights-api';

import { UsagePage } from './usage';

vi.mock('@/lib/insights-api', () => ({
  insightsApi: {
    usage: vi.fn(),
    quota: vi.fn(),
    models: vi.fn(),
    byok: vi.fn(),
    saveByok: vi.fn(),
    deleteByok: vi.fn(),
  },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <UsagePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('UsagePage', () => {
  it('展示今日/本月用量与模型列表', async () => {
    vi.mocked(insightsApi.usage).mockResolvedValue({
      today_tokens: 1200,
      month_tokens: 3000,
      today_events: 2,
      month_events: 3,
      today_cost_usd: 0.0012,
      month_cost_usd: 0.0046,
      unpriced_events: 1,
      by_model: [
        {
          model: 'deepseek/chat',
          tokens: 3000,
          events: 3,
          cost_usd: 0.0046,
          unpriced_events: 1,
        },
      ],
    });
    vi.mocked(insightsApi.models).mockResolvedValue({
      object: 'list',
      default_model: 'deepseek/chat',
      data: [{ id: 'deepseek/chat', object: 'model' }],
    });
    vi.mocked(insightsApi.quota).mockResolvedValue({
      monthly_tokens: 50000,
      max_concurrent_runs: 3,
      used_tokens: 3000,
    });
    renderPage();

    expect(
      (await screen.findAllByText('deepseek/chat')).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(screen.getByText('3,000')).toBeInTheDocument();
    expect(screen.getByText('$0.0012')).toBeInTheDocument();
    expect(screen.getAllByText('$0.0046').length).toBeGreaterThan(0);
    expect(screen.getByText('1 未计价')).toBeInTheDocument();
    expect(screen.getByText('默认')).toBeInTheDocument();
    // 配额条:已用/上限与剩余额度
    expect(await screen.findByText('本月配额')).toBeInTheDocument();
    expect(screen.getByText('3,000 / 50,000 tokens')).toBeInTheDocument();
    expect(screen.getByText('剩余 47,000 tokens')).toBeInTheDocument();
  });

  it('无数据显示明确空态', async () => {
    vi.mocked(insightsApi.usage).mockResolvedValue({
      today_tokens: 0,
      month_tokens: 0,
      today_events: 0,
      month_events: 0,
      today_cost_usd: 0,
      month_cost_usd: 0,
      unpriced_events: 0,
      by_model: [],
    });
    vi.mocked(insightsApi.models).mockResolvedValue({
      object: 'list',
      default_model: 'deepseek/chat',
      data: [],
    });
    vi.mocked(insightsApi.quota).mockResolvedValue({
      monthly_tokens: null,
      max_concurrent_runs: null,
      used_tokens: 0,
    });
    renderPage();

    expect(await screen.findByText('还没有用量数据')).toBeInTheDocument();
    // 未配置配额:如实显示"不限制",而不是 0% 空进度条
    expect(await screen.findByText('未配置配额 · 不限制')).toBeInTheDocument();
  });
});
