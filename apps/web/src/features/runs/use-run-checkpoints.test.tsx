/**
 * P1-C 检查点数据层:列表加载、回退/分叉载荷、写后失效与 409 中文提示。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import type { Run, RunCheckpoint } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import {
  checkpointErrorMessage,
  useRunCheckpoints,
} from './use-run-checkpoints';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    checkpoints: vi.fn(),
    rewind: vi.fn(),
    fork: vi.fn(),
  },
}));

const checkpoint: RunCheckpoint = {
  checkpoint_id: 'ck1',
  step: 3,
  source: 'planner',
  next: ['executor'],
  created_at: '2026-10-01T00:00:00Z',
  parent_checkpoint_id: null,
  runnable: true,
  summary: {
    rounds: 1,
    plan_chars: 20,
    tool_calls: 0,
    todos_count: 0,
    artifact_ready: false,
  },
};

const runAfterRewind: Run = {
  id: 'r1',
  title: '任务',
  goal: '目标',
  status: 'pending',
  rounds: 1,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

function renderModel(onSelectRun = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useRunCheckpoints({ runId: 'r1', onSelectRun }), {
    wrapper,
  });
}

describe('useRunCheckpoints', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(runsApi.checkpoints).mockResolvedValue([checkpoint]);
    vi.mocked(runsApi.rewind).mockResolvedValue(runAfterRewind);
    vi.mocked(runsApi.fork).mockResolvedValue({ ...runAfterRewind, id: 'r2' });
  });

  it('加载检查点列表', async () => {
    const { result } = renderModel();

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.checkpoints).toHaveLength(1);
    expect(result.current.checkpoints[0]?.checkpoint_id).toBe('ck1');
  });

  it('回退提交 checkpoint_id 与可选原因,并刷新列表', async () => {
    const { result } = renderModel();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.openRewind(checkpoint);
      result.current.setReason('计划错了');
    });
    act(() => {
      result.current.confirmRewind();
    });

    await waitFor(() =>
      expect(runsApi.rewind).toHaveBeenCalledWith('r1', {
        checkpoint_id: 'ck1',
        reason: '计划错了',
      }),
    );
    await waitFor(() => expect(result.current.rewindTarget).toBeNull());
    await waitFor(() =>
      expect(runsApi.checkpoints).toHaveBeenCalledTimes(2),
    );
  });

  it('回退原因为空时 body 不带 reason', async () => {
    const { result } = renderModel();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.openRewind(checkpoint);
    });
    act(() => {
      result.current.confirmRewind();
    });

    await waitFor(() =>
      expect(runsApi.rewind).toHaveBeenCalledWith('r1', {
        checkpoint_id: 'ck1',
      }),
    );
  });

  it('分叉成功后切到新 run', async () => {
    const onSelectRun = vi.fn();
    const { result } = renderModel(onSelectRun);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.openFork(checkpoint);
      result.current.setForkTitle('新分支');
    });
    act(() => {
      result.current.confirmFork();
    });

    await waitFor(() =>
      expect(runsApi.fork).toHaveBeenCalledWith('r1', {
        checkpoint_id: 'ck1',
        title: '新分支',
      }),
    );
    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith('r2'));
  });

  it('409 run.checkpoint_not_found 翻成中文提示', async () => {
    vi.mocked(runsApi.rewind).mockRejectedValue(
      new ApiError(
        409,
        'run.checkpoint_not_found',
        'checkpoint 不存在或不属于当前 run',
      ),
    );
    const { result } = renderModel();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.openRewind(checkpoint);
    });
    act(() => {
      result.current.confirmRewind();
    });

    await waitFor(() => expect(result.current.rewindError).toBeTruthy());
    expect(checkpointErrorMessage(result.current.rewindError)).toContain(
      '检查点不存在或不属于当前 run',
    );
  });

  it('其他错误保留后端 message', () => {
    expect(checkpointErrorMessage(new ApiError(500, 'boom', '服务开小差了'))).toBe(
      '服务开小差了',
    );
    expect(checkpointErrorMessage(new Error('网络错误'))).toBe('网络错误');
    expect(checkpointErrorMessage(null)).toBe('操作失败,请重试。');
  });
});
