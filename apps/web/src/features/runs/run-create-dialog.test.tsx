/**
 * P1-C:新建任务弹窗的超时/优先级输入与本地校验(校验失败不发请求)。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { resetRunBoard } from '@/stores/run-board-store';

import { RunCreateDialog } from './run-create-dialog';
import { useRunsModel } from './use-runs-model';

vi.mock('@/lib/runs-api', () => ({
  runsApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
  isUnauthorized: () => false,
}));

const createdRun: Run = {
  id: 'run-new',
  title: '整理本周文档成周报',
  goal: '汇总本周文档,生成周报草稿',
  status: 'pending',
  rounds: 0,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

function Harness() {
  const model = useRunsModel();
  return (
    <>
      <button type="button" onClick={() => model.setCreateOpen(true)}>
        打开新建
      </button>
      <RunCreateDialog model={model} />
    </>
  );
}

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

describe('RunCreateDialog 超时与优先级', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRunBoard();
    vi.mocked(runsApi.list).mockResolvedValue([]);
    vi.mocked(runsApi.create).mockResolvedValue(createdRun);
  });

  it('填写后随创建提交 timeout_seconds 与 priority', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: '打开新建' }));

    const timeout = await screen.findByLabelText('超时(秒)');
    const priority = screen.getByLabelText('优先级');
    fireEvent.change(timeout, { target: { value: '90' } });
    fireEvent.change(priority, { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(runsApi.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(runsApi.create).mock.calls[0]?.[0]).toMatchObject({
      timeout_seconds: 90,
      priority: 10,
    });
  });

  it('留空超时时不落键(平台默认)', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: '打开新建' }));
    await screen.findByLabelText('超时(秒)');

    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(runsApi.create).toHaveBeenCalledTimes(1));
    const body = vi.mocked(runsApi.create).mock.calls[0]?.[0];
    expect(body).not.toHaveProperty('timeout_seconds');
    expect(body?.priority).toBe(0);
  });

  it('优先级越界时就地报错且不发请求', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: '打开新建' }));
    const priority = await screen.findByLabelText('优先级');
    fireEvent.change(priority, { target: { value: '200' } });

    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /优先级必须是 -100\.\.100 的整数/,
    );
    expect(runsApi.create).not.toHaveBeenCalled();
  });
});
