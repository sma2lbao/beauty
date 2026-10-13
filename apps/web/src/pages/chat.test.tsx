import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Run, RunEvent } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { ChatPage } from './chat';

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      list: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      getPlan: vi.fn(),
      events: vi.fn(),
      generatePlan: vi.fn(),
      updatePlan: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      resume: vi.fn(),
      retry: vi.fn(),
      confirm: vi.fn(),
      artifact: vi.fn(),
      instructions: vi.fn(),
      queueInstruction: vi.fn(),
    },
    streamRunEvents: vi.fn(),
  };
});

// SSE 管道本身由控制台测试覆盖;这里只测"事件 → 对话"的映射与就地动作
let currentEvents: RunEvent[] = [];
vi.mock('@/components/runs/use-run-events', () => ({
  useRunEvents: () => ({ events: currentEvents, connected: true }),
}));

vi.mock('@/components/runs/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周邮件成周报',
    goal: '把本周邮件整理成周报',
    status: 'awaiting_approval',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: [],
    status_reason: null,
    parent_run_id: null,
    created_at: '2026-10-13T10:00:00+00:00',
    ...overrides,
  };
}

function event(seq: number, type: string, payload: Record<string, unknown>): RunEvent {
  return { seq, type, payload };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function typeAndSend(text: string) {
  fireEvent.change(screen.getByLabelText('说点什么'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: '发送' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  currentEvents = [];
  vi.mocked(runsApi.list).mockResolvedValue([]);
  vi.mocked(runsApi.create).mockResolvedValue(makeRun({ status: 'pending' }));
  vi.mocked(runsApi.get).mockResolvedValue(makeRun());
});

afterEach(() => {
  currentEvents = [];
});

describe('ChatPage', () => {
  it('空态:给出引导且没有对话时不能发送', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('对话');
    expect(
      screen.getByText(/在下面说说你要做什么,例如「把本周邮件整理成周报」/),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
  });

  it('发送消息 = 新建任务,并把消息留在对话里', async () => {
    vi.mocked(runsApi.create).mockResolvedValue(
      makeRun({ id: 'run-9', status: 'pending' }),
    );
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ id: 'run-9', status: 'pending' }));
    renderPage();

    typeAndSend('把本周邮件整理成周报');

    await waitFor(() =>
      expect(runsApi.create).toHaveBeenCalledWith({
        title: '把本周邮件整理成周报',
        goal: '把本周邮件整理成周报',
        acceptance_criteria: [],
      }),
    );
    expect(await screen.findByText('把本周邮件整理成周报')).toBeTruthy();
    expect(await screen.findByText(/待规划/)).toBeTruthy();
  });

  it('任务进行中:发送变成追加指令,不新开一跑', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([makeRun()]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun());
    vi.mocked(runsApi.queueInstruction).mockResolvedValue({
      id: 'i1',
      run_id: 'run-1',
      content: '顺便看看日历',
      status: 'queued',
      created_at: '2026-10-13T10:01:00+00:00',
    });
    renderPage();

    // 切到进行中的任务
    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    await screen.findByRole('button', { name: '补充指令' });

    fireEvent.change(screen.getByLabelText('说点什么'), {
      target: { value: '顺便看看日历' },
    });
    fireEvent.click(screen.getByRole('button', { name: '补充指令' }));

    await waitFor(() =>
      expect(runsApi.queueInstruction).toHaveBeenCalledWith('run-1', '顺便看看日历'),
    );
    expect(runsApi.create).not.toHaveBeenCalled();
  });

  it('待审批:显示放行/拒绝,放行调用 approve', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([makeRun()]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun());
    vi.mocked(runsApi.approve).mockResolvedValue(makeRun({ status: 'running' }));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    fireEvent.click(await screen.findByRole('button', { name: '放行执行' }));

    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认写操作:确认按钮调用 confirm(approve)', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'awaiting_confirmation' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    vi.mocked(runsApi.confirm).mockResolvedValue(makeRun({ status: 'running' }));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确认执行写操作' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith('run-1', 'approve'),
    );
  });

  it('把 run 事件翻译成对话内容(工具调用/交付/失败)', async () => {
    currentEvents = [
      event(1, 'run.created.v1', { title: '整理本周邮件成周报' }),
      event(2, 'run.planned.v1', { attempt: 1 }),
      event(3, 'run.tool.called.v1', { name: 'fetch_emails', ok: true }),
      event(4, 'run.delivered.v1', { title: '周报' }),
      event(5, 'run.completed.v1', {}),
    ];
    vi.mocked(runsApi.list).mockResolvedValue([makeRun()]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));

    expect(await screen.findByText(/已建立任务《整理本周邮件成周报》/)).toBeTruthy();
    expect(screen.getByText(/计划已就绪/)).toBeTruthy();
    expect(screen.getByText(/调用工具 fetch_emails —成功/)).toBeTruthy();
    expect(screen.getByText(/已交付《周报》/)).toBeTruthy();
    expect(screen.getByText('任务完成 ✅')).toBeTruthy();
  });

  it('失败事件以错误语气展示,并提示去控制台处理', async () => {
    currentEvents = [
      event(1, 'run.failed.v1', { stage: 'execute', reason: 'UpstreamError: 上游 500' }),
    ];
    vi.mocked(runsApi.list).mockResolvedValue([makeRun()]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'failed', status_reason: 'UpstreamError: 上游 500' }),
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));

    expect(
      await screen.findByText(/执行失败\(execute\):UpstreamError: 上游 500/),
    ).toBeTruthy();
    // 详情是异步取的,必须用 findBy*(否则会快照到「还没开始的任务」)
    expect(
      await screen.findByText('失败原因:UpstreamError: 上游 500'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: '运行控制台' })).toHaveAttribute(
      'href',
      '/runs',
    );
  });

  it('完成后按需取回成品正文', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ status: 'completed' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(runsApi.artifact).mockResolvedValue({
      id: 'a1',
      run_id: 'run-1',
      kind: 'markdown',
      title: '周报',
      content: '# 本周周报\n\n- 邮件已汇总',
      created_at: '2026-10-13T10:05:00+00:00',
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    fireEvent.click(await screen.findByRole('button', { name: '查看成品' }));

    await waitFor(() => expect(runsApi.artifact).toHaveBeenCalledWith('run-1'));
    expect(await screen.findByText(/# 本周周报/)).toBeTruthy();
  });

  it('最近任务里可以切换对话', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([
      makeRun({ id: 'run-1', title: '第一个任务' }),
      makeRun({ id: 'run-2', title: '第二个任务', status: 'completed' }),
    ]);
    vi.mocked(runsApi.get).mockImplementation(async (id) =>
      id === 'run-2'
        ? makeRun({ id: 'run-2', title: '第二个任务', status: 'completed' })
        : makeRun({ id: 'run-1', title: '第一个任务' }),
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /第二个任务/ }));

    expect(await screen.findByText('第二个任务 · 已完成')).toBeTruthy();
    expect(runsApi.get).toHaveBeenCalledWith('run-2');
  });

  it('新对话清空当前选择', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([makeRun()]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun());
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    await screen.findByText('整理本周邮件成周报 · 待审批');

    fireEvent.click(screen.getByRole('button', { name: '新对话' }));

    expect(await screen.findByText('还没开始的任务')).toBeTruthy();
    expect(screen.getByRole('button', { name: '新对话' })).toBeDisabled();
  });
});
