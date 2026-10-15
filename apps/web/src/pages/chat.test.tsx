import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Conversation, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import type { Run, RunEvent } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import { ChatPage } from './chat';

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    messages: vi.fn(),
    postMessage: vi.fn(),
  },
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      get: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      confirm: vi.fn(),
      artifact: vi.fn(),
    },
  };
});

// SSE 管道由控制台测试覆盖;这里只测"事件 → 对话"的映射与就地动作
let currentEvents: RunEvent[] = [];
vi.mock('@/components/runs/use-run-events', () => ({
  useRunEvents: () => ({ events: currentEvents, connected: true }),
}));

vi.mock('@/components/runs/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    title: '整理本周邮件成周报',
    created_at: '2026-10-13T10:00:00+00:00',
    updated_at: '2026-10-13T10:05:00+00:00',
    active_run_id: 'run-1',
    ...overrides,
  };
}

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm1',
    conversation_id: 'c1',
    run_id: 'run-1',
    role: 'user',
    content: '把本周邮件整理成周报',
    created_at: '2026-10-13T10:01:00+00:00',
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周邮件成周报',
    goal: '汇总本周邮件,生成周报草稿',
    status: 'awaiting_approval',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: [],
    status_reason: null,
    parent_run_id: null,
    conversation_id: 'c1',
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
  vi.mocked(conversationsApi.list).mockResolvedValue([makeConversation()]);
  vi.mocked(conversationsApi.create).mockResolvedValue(
    makeConversation({ id: 'c-new', title: '新对话', active_run_id: undefined }),
  );
  vi.mocked(conversationsApi.get).mockResolvedValue(makeConversation());
  vi.mocked(conversationsApi.messages).mockResolvedValue([makeMessage()]);
  vi.mocked(conversationsApi.postMessage).mockResolvedValue({
    message: makeMessage({
      id: 'm9',
      conversation_id: 'c-new',
      run_id: 'run-9',
      content: '把本周邮件整理成周报',
    }),
    run: makeRun({ id: 'run-9', status: 'awaiting_approval', conversation_id: 'c-new' }),
    reply: null,
    mode: 'run_created',
  });
  vi.mocked(runsApi.get).mockResolvedValue(makeRun());
});

afterEach(() => {
  currentEvents = [];
});

describe('ChatPage', () => {
  it('空态:给出引导且没有内容时不能发送', async () => {
    vi.mocked(conversationsApi.list).mockResolvedValue([]);
    renderPage();

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('对话');
    expect(
      screen.getByText(/在下面说说你要做什么,例如「把本周邮件整理成周报」/),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新对话' })).toBeDisabled();
    expect(await screen.findByText('还没有对话。')).toBeTruthy();
  });

  it('发送首条消息:先建会话再发消息,消息与状态出现在对话里', async () => {
    renderPage();

    typeAndSend('把本周邮件整理成周报');

    await waitFor(() => expect(conversationsApi.create).toHaveBeenCalled());
    await waitFor(() =>
      expect(conversationsApi.postMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周邮件整理成周报',
        'auto',
      ),
    );
    // 返回的 run 立即接管:状态从它读
    await waitFor(() => expect(runsApi.get).toHaveBeenCalledWith('run-9'));
    expect(await screen.findByText(/待审批/)).toBeTruthy();
  });

  it('在已有会话里发送:不重复建会话,消息走 postMessage', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    await screen.findByText('待审批'); // 等 run 载入(在途状态)

    fireEvent.change(screen.getByLabelText('说点什么'), {
      target: { value: '顺便看看日历' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(conversationsApi.postMessage).toHaveBeenCalledWith(
        'c1',
        '顺便看看日历',
        'auto',
      ),
    );
    expect(conversationsApi.create).not.toHaveBeenCalled();
  });

  it('消息历史:user 与 assistant 气泡都渲染', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage(),
      makeMessage({
        id: 'a1',
        role: 'assistant',
        content: '# 周报\n\n- 邮件已汇总',
      }),
    ]);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));

    expect(await screen.findByText('把本周邮件整理成周报')).toBeTruthy();
    expect(await screen.findByText(/# 周报/)).toBeTruthy();
  });

  it('待审批:显示放行/拒绝,放行调用 approve', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    fireEvent.click(await screen.findByRole('button', { name: '放行执行' }));

    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认写操作:确认按钮调用 confirm(approve)', async () => {
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ active_run_id: 'run-1' }),
    );
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
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
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'failed', status_reason: 'UpstreamError: 上游 500' }),
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));

    expect(
      await screen.findByText(/执行失败\(execute\):UpstreamError: 上游 500/),
    ).toBeTruthy();
    expect(
      await screen.findByText('失败原因:UpstreamError: 上游 500'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: '运行控制台' })).toHaveAttribute(
      'href',
      '/runs',
    );
  });

  it('完成后按需取回成品正文', async () => {
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

  it('最近对话里可以切换会话', async () => {
    vi.mocked(conversationsApi.list).mockResolvedValue([
      makeConversation(),
      makeConversation({
        id: 'c2',
        title: '第二个对话',
        active_run_id: null,
      }),
    ]);
    vi.mocked(conversationsApi.get).mockImplementation(async (id) =>
      id === 'c2'
        ? makeConversation({ id: 'c2', title: '第二个对话', active_run_id: null })
        : makeConversation(),
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /第二个对话/ }));

    // 标题同时出现在侧栏与卡片头(详情解析后)
    await waitFor(() =>
      expect(screen.getAllByText('第二个对话')).toHaveLength(2),
    );
    expect(conversationsApi.get).toHaveBeenCalledWith('c2');
    expect(runsApi.get).not.toHaveBeenCalled(); // 无进行中的 run,不挂状态/事件流
  });

  it('新对话清空当前选择', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /整理本周邮件成周报/ }));
    await screen.findByText('待审批');

    fireEvent.click(screen.getByRole('button', { name: '新对话' }));

    expect(await screen.findByText('还没开始的对话')).toBeTruthy();
    expect(screen.getByRole('button', { name: '新对话' })).toBeDisabled();
  });

  it('简单对话:只回一条,不建任务、不查状态', async () => {
    vi.mocked(conversationsApi.create).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新对话', active_run_id: null }),
    );
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新对话', active_run_id: null }),
    );
    vi.mocked(conversationsApi.postMessage).mockResolvedValue({
      message: makeMessage({
        id: 'm2',
        conversation_id: 'c-new',
        run_id: null,
        content: '你好',
      }),
      run: null,
      reply: makeMessage({
        id: 'a2',
        conversation_id: 'c-new',
        run_id: null,
        role: 'assistant',
        content: '你好!有什么可以帮你的?',
      }),
      mode: 'chat',
    });
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage({
        id: 'm2',
        conversation_id: 'c-new',
        run_id: null,
        content: '你好',
      }),
      makeMessage({
        id: 'a2',
        conversation_id: 'c-new',
        run_id: null,
        role: 'assistant',
        content: '你好!有什么可以帮你的?',
      }),
    ]);
    renderPage();

    typeAndSend('你好');

    expect(await screen.findByText('你好!有什么可以帮你的?')).toBeTruthy();
    expect(conversationsApi.postMessage).toHaveBeenCalledWith(
      'c-new',
      '你好',
      'auto',
    );
    // 没有 run:不查状态、不显示任务状态
    expect(runsApi.get).not.toHaveBeenCalled();
    expect(screen.queryByText('待审批')).toBeNull();
  });

  it('发送方式选「对话」时按 chat 发送', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('发送方式'), {
      target: { value: 'chat' },
    });
    typeAndSend('你好');

    await waitFor(() =>
      expect(conversationsApi.postMessage).toHaveBeenCalledWith(
        'c-new',
        '你好',
        'chat',
      ),
    );
  });

  it('发送方式选「任务」时按 task 发送', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('发送方式'), {
      target: { value: 'task' },
    });
    typeAndSend('把本周邮件整理成周报');

    await waitFor(() =>
      expect(conversationsApi.postMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周邮件整理成周报',
        'task',
      ),
    );
  });
});
