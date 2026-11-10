/**
 * P1-A §7.2:run 详情危险确认的三档授权。
 *
 * 在操作区组件层面测:默认「本次允许」,切「本会话允许这些工具」后
 * grant 随 runsApi.confirm 透传;拒绝不携带 grant。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { runsApi } from '@/lib/runs-api';

import { RunDetailActions } from './run-detail-actions';
import type { RunDetailModel } from './use-run-detail';

vi.mock('@/lib/runs-api', () => ({
  runsApi: { confirm: vi.fn() },
}));

function makeModel(overrides: Partial<RunDetailModel> = {}): RunDetailModel {
  const mutation = {
    mutate: (fn: () => void) => fn(),
    isPending: false,
    isError: false,
    error: null,
  };
  return {
    action: mutation,
    canCancel: false,
    cancelMutation: mutation,
    editable: false,
    rejectReason: '',
    retryMutation: mutation,
    runId: 'run-1',
    setRejectReason: vi.fn(),
    setShowArtifact: vi.fn(),
    status: 'awaiting_confirmation',
    ...overrides,
  } as unknown as RunDetailModel;
}

describe('RunDetailActions 危险确认授权范围', () => {
  it('默认按「本次允许」确认', async () => {
    render(<RunDetailActions model={makeModel()} />);

    fireEvent.click(
      screen.getByRole('button', { name: '确认执行危险操作' }),
    );

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith(
        'run-1',
        'approve',
        undefined,
        'once',
      ),
    );
  });

  it('选「本会话允许这些工具」后确认,grant 透传 session', async () => {
    render(<RunDetailActions model={makeModel()} />);

    fireEvent.click(screen.getByRole('button', { name: '本会话允许这些工具' }));
    fireEvent.click(screen.getByRole('button', { name: '确认执行危险操作' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith(
        'run-1',
        'approve',
        undefined,
        'session',
      ),
    );
  });

  it('选「始终允许这些工具」后确认,grant 透传 always', async () => {
    render(<RunDetailActions model={makeModel()} />);

    fireEvent.click(
      screen.getByRole('button', { name: '始终允许这些工具' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '确认执行危险操作' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith(
        'run-1',
        'approve',
        undefined,
        'always',
      ),
    );
  });

  it('拒绝危险操作不携带 grant', async () => {
    render(<RunDetailActions model={makeModel()} />);

    fireEvent.click(screen.getByRole('button', { name: '拒绝危险操作' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith('run-1', 'reject'),
    );
  });
});
