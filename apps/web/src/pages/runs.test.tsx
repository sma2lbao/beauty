import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run } from '@/lib/runs-api';
import { ApiError, runsApi, streamRunEvents } from '@/lib/runs-api';
import { resetRunBoard } from '@/stores/run-board-store';

import { RunsPage } from './runs';

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual, // 保留 ApiError / parseEventFrame / mergeEvents 等纯函数
    runsApi: {
      list: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      getPlan: vi.fn(),
      steps: vi.fn(async () => []),
      events: vi.fn(),
      generatePlan: vi.fn(),
      updatePlan: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      confirm: vi.fn(),
      resume: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
      artifact: vi.fn(),
      artifacts: vi.fn(),
      downloadArtifact: vi.fn(),
      instructions: vi.fn(),
      queueInstruction: vi.fn(),
    },
    streamRunEvents: vi.fn(),
  };
});

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周邮件成周报',
    goal: '汇总本周邮件,生成周报草稿',
    status: 'pending',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: ['覆盖 5 个工作日'],
    status_reason: null,
    parent_run_id: null,
    conversation_id: null,
    created_at: '2026-10-12T10:00:00+00:00',
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function selectRun(title: string) {
  fireEvent.click(
    await screen.findByRole('button', { name: new RegExp(title) }),
  );
}

describe('RunsPage', () => {
  beforeEach(() => {
    resetRunBoard();
    vi.mocked(streamRunEvents).mockImplementation(
      () => new Promise<void>(() => {}), // 事件流挂起:测试不依赖 SSE
    );
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'pending',
      plan_markdown: '',
    });
    vi.mocked(runsApi.instructions).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('没有 run 时给出空态', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('还没有 run。')).toBeInTheDocument();
  });

  it('接口返回 401:提示先完成鉴权', async () => {
    vi.mocked(runsApi.list).mockRejectedValue(
      new ApiError(401, 'auth.token_missing', '缺少 Bearer token'),
    );
    renderPage();

    expect(await screen.findByText(/未登录或令牌已失效/)).toBeInTheDocument();
  });

  it('创建 run 时把验收标准按行解析', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([]);
    vi.mocked(runsApi.create).mockResolvedValue(makeRun({ id: 'run-2' }));
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ id: 'run-2' }));
    renderPage();

    fireEvent.click(
      (await screen.findAllByRole('button', { name: '新建任务' }))[0],
    );
    fireEvent.click(await screen.findByRole('button', { name: '创建' }));

    await waitFor(() =>
      expect(runsApi.create).toHaveBeenCalledWith({
        title: '整理本周文档成周报',
        goal: '汇总本周文档,生成周报草稿',
        acceptance_criteria: ['覆盖本周主要进展', '不超过 300 字'],
      }),
    );
  });

  it('待审批 run:显示计划与放行按钮,放行调用审批 API', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'awaiting_approval' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_approval' }),
    );
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 执行计划\n- [ ] 覆盖 5 个工作日',
    });
    vi.mocked(runsApi.approve).mockResolvedValue(
      makeRun({ status: 'running' }),
    );
    renderPage();

    await selectRun('整理本周邮件成周报');
    expect(await screen.findByTestId('run-status')).toHaveTextContent('待审批');
    expect(await screen.findByLabelText('计划内容')).toHaveValue(
      '# 执行计划\n- [ ] 覆盖 5 个工作日',
    );

    fireEvent.click(screen.getByRole('button', { name: '放行执行' }));
    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认危险操作 run:确认按钮调用 confirm(approve)', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'awaiting_confirmation' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    vi.mocked(runsApi.confirm).mockResolvedValue(
      makeRun({ status: 'running' }),
    );
    renderPage();

    await selectRun('整理本周邮件成周报');
    expect(await screen.findByTestId('run-status')).toHaveTextContent(
      '待确认危险操作',
    );

    fireEvent.click(screen.getByRole('button', { name: '确认执行危险操作' }));
    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith('run-1', 'approve'),
    );
  });

  it('已完成 run:列出多件交付物(报告 + 文件)', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'completed' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(runsApi.artifacts).mockResolvedValue([
      {
        id: 'a1',
        run_id: 'run-1',
        kind: 'markdown',
        title: '周报',
        filename: 'weekly.md',
        content_type: 'text/markdown',
        size_bytes: 2048,
        storage: 'filesystem',
        source: 'report',
        created_at: '2026-10-12T11:00:00+00:00',
      },
      {
        id: 'a2',
        run_id: 'run-1',
        kind: 'attachment',
        title: '运行截图',
        filename: 'screen.png',
        content_type: 'image/png',
        size_bytes: 512,
        storage: 'filesystem',
        source: 'tool',
        created_at: '2026-10-12T11:01:00+00:00',
      },
    ]);
    renderPage();

    await selectRun('整理本周邮件成周报');
    fireEvent.click(await screen.findByRole('button', { name: '查看交付物' }));

    expect(await screen.findByText('weekly.md')).toBeInTheDocument();
    expect(screen.getByText('screen.png')).toBeInTheDocument();
    expect(screen.getByText('2 件')).toBeInTheDocument();
    expect(screen.getByText(/2\.0 KB/)).toBeInTheDocument();
    expect(screen.getByText(/512 B/)).toBeInTheDocument();
    expect(runsApi.artifacts).toHaveBeenCalledWith('run-1');
  });

  it('交付物:文本可预览、图片用 blob URL、文件可下载', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'completed' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(runsApi.artifacts).mockResolvedValue([
      {
        id: 'a1',
        run_id: 'run-1',
        kind: 'markdown',
        title: '周报',
        filename: 'weekly.md',
        content_type: 'text/markdown',
        size_bytes: 2048,
        storage: 'filesystem',
        created_at: '2026-10-12T11:00:00+00:00',
      },
      {
        id: 'a2',
        run_id: 'run-1',
        kind: 'attachment',
        title: '运行截图',
        filename: 'screen.png',
        content_type: 'image/png',
        size_bytes: 512,
        storage: 'filesystem',
        created_at: '2026-10-12T11:01:00+00:00',
      },
    ]);
    vi.mocked(runsApi.downloadArtifact).mockImplementation(async (_, id) =>
      id === 'a2'
        ? new Blob(['PNG'], { type: 'image/png' })
        : new Blob(['# 本周周报\n\n已汇总。'], { type: 'text/markdown' }),
    );
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:mock-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});
    renderPage();

    await selectRun('整理本周邮件成周报');
    fireEvent.click(await screen.findByRole('button', { name: '查看交付物' }));

    fireEvent.click(
      await screen.findByRole('button', { name: '预览 weekly.md' }),
    );
    expect(
      await screen.findByRole('heading', { name: '本周周报' }),
    ).toBeInTheDocument();
    expect(screen.getByText('已汇总。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '预览 screen.png' }));
    expect(
      await screen.findByRole('img', { name: '运行截图' }),
    ).toHaveAttribute('src', 'blob:mock-image');

    fireEvent.click(screen.getByRole('button', { name: '下载 screen.png' }));
    await waitFor(() =>
      expect(runsApi.downloadArtifact).toHaveBeenCalledWith('run-1', 'a2'),
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
  });

  it('blocked run:展示中断原因,可重新进闸(resume)', async () => {
    const blocked = makeRun({
      status: 'blocked',
      status_reason: '计划与目标不符,先别发',
    });
    vi.mocked(runsApi.list).mockResolvedValue([blocked]);
    vi.mocked(runsApi.get).mockResolvedValue(blocked);
    vi.mocked(runsApi.resume).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 计划',
    });
    renderPage();

    await selectRun('整理本周邮件成周报');
    expect(await screen.findByTestId('run-status')).toHaveTextContent('已阻塞');
    expect(screen.getByText(/中断原因:计划与目标不符/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新规划并进闸' }));
    await waitFor(() => expect(runsApi.resume).toHaveBeenCalledWith('run-1'));
  });

  it('cancelled run:看板与详情展示已取消,取消按钮消失', async () => {
    const cancelled = makeRun({
      status: 'cancelled',
      status_reason: '用户在控制台取消',
    });
    vi.mocked(runsApi.list).mockResolvedValue([cancelled]);
    vi.mocked(runsApi.get).mockResolvedValue(cancelled);
    renderPage();

    expect(await screen.findByText('已取消 · 用户在控制台取消')).toBeTruthy();

    await selectRun('整理本周邮件成周报');
    expect(await screen.findByTestId('run-status')).toHaveTextContent('已取消');
    expect(screen.getByText(/取消原因:用户在控制台取消/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '取消任务' })).toBeNull();
  });

  it('cancelled run:追加指令不可提交(已是终态)', async () => {
    const cancelled = makeRun({ status: 'cancelled' });
    vi.mocked(runsApi.list).mockResolvedValue([cancelled]);
    vi.mocked(runsApi.get).mockResolvedValue(cancelled);
    renderPage();

    await selectRun('整理本周邮件成周报');
    await screen.findByTestId('run-status');
    fireEvent.change(await screen.findByLabelText('追加指令'), {
      target: { value: '再补一句' },
    });
    expect(screen.getByRole('button', { name: '提交指令' })).toBeDisabled();
  });

  it('blocked run:追加指令仍可用(blocked 非终态)', async () => {
    const blocked = makeRun({ status: 'blocked' });
    vi.mocked(runsApi.list).mockResolvedValue([blocked]);
    vi.mocked(runsApi.get).mockResolvedValue(blocked);
    vi.mocked(runsApi.instructions).mockResolvedValue([]);
    renderPage();

    await selectRun('整理本周邮件成周报');
    await screen.findByTestId('run-status');

    const submit = screen.getByRole('button', { name: '提交指令' });
    expect(submit).toBeDisabled(); // 没填内容时不可提交

    fireEvent.change(await screen.findByLabelText('追加指令'), {
      target: { value: '换个更短的计划' },
    });
    // 状态更新后重新取一次按钮(旧引用不会自动刷新)
    expect(screen.getByRole('button', { name: '提交指令' })).toBeEnabled(); // blocked 不是终态:仍可排队
  });

  it('failed run:展示失败原因,重试后切到新 run', async () => {
    const failed = makeRun({
      status: 'failed',
      status_reason: 'RuntimeError: 模型挂了',
    });
    const retried = makeRun({
      id: 'run-2',
      title: '整理本周邮件成周报(重试)',
      status: 'awaiting_approval',
      parent_run_id: 'run-1',
    });
    vi.mocked(runsApi.list).mockResolvedValue([failed]);
    vi.mocked(runsApi.get).mockImplementation(async (id) =>
      id === 'run-2' ? retried : failed,
    );
    vi.mocked(runsApi.retry).mockResolvedValue(retried);
    renderPage();

    await selectRun('整理本周邮件成周报');
    expect(
      await screen.findByText(/失败原因:RuntimeError: 模型挂了/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重试(另起一跑)' }));
    await waitFor(() => expect(runsApi.retry).toHaveBeenCalledWith('run-1'));
    // 成功后切到新 run,并显示血缘
    expect(await screen.findByText(/由 run run-1… 重试而来/)).toBeTruthy();
  });

  it('追加指令:提交后调用排队接口并展示队列状态', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'awaiting_approval' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_approval' }),
    );
    vi.mocked(runsApi.queueInstruction).mockResolvedValue({
      id: 'i1',
      run_id: 'run-1',
      content: '也看看日历',
      status: 'queued',
      created_at: '2026-10-12T10:05:00+00:00',
    });
    // 首次加载为空;排队成功后的刷新(失效重取)才带回队列项
    vi.mocked(runsApi.instructions)
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        {
          id: 'i1',
          run_id: 'run-1',
          content: '也看看日历',
          status: 'queued',
          created_at: '2026-10-12T10:05:00+00:00',
        },
      ]);
    renderPage();

    await selectRun('整理本周邮件成周报');
    fireEvent.change(await screen.findByLabelText('追加指令'), {
      target: { value: '也看看日历' },
    });
    fireEvent.click(screen.getByRole('button', { name: '提交指令' }));

    await waitFor(() =>
      expect(runsApi.queueInstruction).toHaveBeenCalledWith(
        'run-1',
        '也看看日历',
      ),
    );
    expect(await screen.findByText('待消费')).toBeInTheDocument();
  });
});
