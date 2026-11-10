import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AdminCostSummary } from '@/lib/admin-api';
import { ApiError } from '@/lib/http';

import { AdminCostPanel } from './admin-cost-panel';
import type { AdminCostModel } from './use-admin-cost-model';

function makeSummary(
  overrides: Partial<AdminCostSummary> = {},
): AdminCostSummary {
  return {
    days: 30,
    since: '2026-10-12T00:00:00Z',
    totals: {
      events: 120,
      tokens: 45000,
      cost_usd: 1.23,
      unpriced_events: 4,
      platform_events: 100,
      byok_events: 20,
    },
    cost_by_source: [
      { source: 'platform', events: 100, cost_usd: 1.1 },
      { source: 'byok', events: 20, cost_usd: 0.13 },
    ],
    by_day: [
      {
        date: '2026-10-12',
        events: 2,
        tokens: 100,
        cost_usd: 0.2,
        unpriced_events: 0,
      },
      {
        date: '2026-10-13',
        events: 3,
        tokens: 150,
        cost_usd: 0.4,
        unpriced_events: 1,
      },
    ],
    by_model: [
      {
        model: 'gpt-x',
        events: 80,
        tokens: 30000,
        cost_usd: 0.9,
        unpriced_events: 0,
      },
    ],
    top_accounts: [
      {
        account_id: 'b'.repeat(32),
        email: 'ops@example.com',
        events: 40,
        tokens: 12000,
        cost_usd: 0.5,
      },
    ],
    ...overrides,
  };
}

function makeModel(overrides: Partial<AdminCostModel> = {}): AdminCostModel {
  return {
    range: 30,
    setRange: vi.fn(),
    data: makeSummary(),
    isLoading: false,
    error: null,
    access: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

describe('AdminCostPanel 渲染', () => {
  it('统计卡、来源拆分、按天条形、按模型与 Top 账号', () => {
    render(<AdminCostPanel model={makeModel()} />);

    expect(screen.getByTestId('admin-cost-total-cost')).toHaveTextContent(
      '$1.23',
    );
    expect(screen.getByTestId('admin-cost-total-events')).toHaveTextContent(
      '120',
    );
    expect(screen.getByTestId('admin-cost-total-tokens')).toHaveTextContent(
      '45,000',
    );
    expect(screen.getByTestId('admin-cost-total-unpriced')).toHaveTextContent(
      '4',
    );
    expect(
      screen.getByTestId('admin-cost-total-unpriced-badge'),
    ).toHaveTextContent('未计价');
    expect(screen.getByText(/自 2026-10-12 起/)).toBeInTheDocument();

    expect(
      screen.getByTestId('admin-cost-source-platform'),
    ).toHaveTextContent('平台承担');
    expect(
      screen.getByTestId('admin-cost-source-platform'),
    ).toHaveTextContent('$1.10');
    expect(screen.getByTestId('admin-cost-source-byok')).toHaveTextContent(
      'BYOK(自带 key)',
    );
    expect(screen.getByTestId('admin-cost-source-byok')).toHaveTextContent(
      '$0.13',
    );

    const bars = screen.getAllByTestId('admin-cost-day-bar');
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveAttribute(
      'title',
      expect.stringContaining('2026-10-12'),
    );
    expect(bars[0]).toHaveAttribute('title', expect.stringContaining('$0.2000'));
    expect(bars[0]).toHaveAttribute('title', expect.stringContaining('2 次调用'));

    expect(screen.getByTestId('admin-cost-model-row')).toHaveTextContent(
      'gpt-x',
    );
    expect(screen.getByTestId('admin-cost-model-row')).toHaveTextContent(
      '$0.90',
    );
    expect(
      screen.getByTestId('admin-cost-account-row'),
    ).toHaveTextContent('ops@example.com');
  });

  it('email 缺失时退回账号 id 前缀', () => {
    render(
      <AdminCostPanel
        model={makeModel({
          data: makeSummary({
            top_accounts: [
              {
                account_id: '0123456789abcdef',
                email: null,
                events: 1,
                tokens: 2,
                cost_usd: 0.01,
              },
            ],
          }),
        })}
      />,
    );

    expect(
      screen.getByTestId('admin-cost-account-row'),
    ).toHaveTextContent('01234567…');
  });

  it('范围切换回调 setRange,当前范围 aria-pressed', () => {
    const setRange = vi.fn();
    render(<AdminCostPanel model={makeModel({ range: 30, setRange })} />);

    expect(
      screen.getByRole('button', { name: '最近 30 天' }),
    ).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '最近 7 天' }));
    expect(setRange).toHaveBeenCalledWith(7);
  });
});

describe('AdminCostPanel 状态', () => {
  it('加载中显示占位', () => {
    render(<AdminCostPanel model={makeModel({ data: undefined, isLoading: true })} />);

    expect(screen.getByText('加载成本汇总…')).toBeInTheDocument();
  });

  it('空窗口显示三处空态', () => {
    render(
      <AdminCostPanel
        model={makeModel({
          data: makeSummary({
            totals: {
              events: 0,
              tokens: 0,
              cost_usd: 0,
              unpriced_events: 0,
              platform_events: 0,
              byok_events: 0,
            },
            cost_by_source: [],
            by_day: [],
            by_model: [],
            top_accounts: [],
          }),
        })}
      />,
    );

    expect(screen.getByText('窗口内没有成本数据')).toBeInTheDocument();
    expect(screen.getByText('窗口内没有模型调用记录。')).toBeInTheDocument();
    expect(screen.getByText('窗口内没有账号成本记录。')).toBeInTheDocument();
  });

  it('读取失败就地提示并可重试', () => {
    const refetch = vi.fn();
    render(
      <AdminCostPanel
        model={makeModel({
          data: undefined,
          error: new Error('服务不可用'),
          refetch,
        })}
      />,
    );

    expect(
      screen.getByText('读取成本汇总失败:服务不可用'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('403 沿用无权限面板,不渲染成本内容', () => {
    const refetch = vi.fn();
    render(
      <AdminCostPanel
        model={makeModel({
          data: undefined,
          error: new ApiError(403, 'admin.required', '需要管理员权限'),
          access: 'forbidden',
          refetch,
        })}
      />,
    );

    expect(screen.getByText('无管理员权限')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-cost-panel')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('admin-cost-total-cost'),
    ).not.toBeInTheDocument();
  });
});
