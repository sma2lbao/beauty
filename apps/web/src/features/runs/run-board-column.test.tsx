/**
 * P1-C:看板卡片上的超时徽标与失败原因(只对 status_reason 命中「执行超时」的 failed run)。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Run } from '@/lib/runs-api';

import { RunBoardColumn } from './run-board-column';

const baseRun: Run = {
  id: 'run-1',
  title: '修复登录超时',
  goal: '修复登录 30s 超时',
  status: 'failed',
  rounds: 2,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

describe('RunBoardColumn 超时徽标', () => {
  it('超时失败的卡片显示「超时」徽标与原因', () => {
    render(
      <RunBoardColumn
        label="失败"
        tone="danger"
        runs={[
          {
            ...baseRun,
            timeout_seconds: 120,
            status_reason: '执行超时（120 秒），可重试',
          },
        ]}
        selectedRunId={null}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByTestId('run-timeout-badge')).toHaveTextContent('超时');
    expect(screen.getByText(/失败 · 执行超时/)).toBeInTheDocument();
  });

  it('普通失败的卡片不显示超时徽标', () => {
    render(
      <RunBoardColumn
        label="失败"
        tone="danger"
        runs={[{ ...baseRun, status_reason: '计划校验失败' }]}
        selectedRunId={null}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('run-timeout-badge')).not.toBeInTheDocument();
    expect(screen.getByText(/失败 · 计划校验失败/)).toBeInTheDocument();
  });
});
