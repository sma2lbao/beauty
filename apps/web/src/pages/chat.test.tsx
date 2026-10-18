import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Conversation, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
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
  streamMessage: vi.fn(),
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      get: vi.fn(),
      getPlan: vi.fn(),
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

function event(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
): RunEvent {
  return { seq, type, payload };
}

function renderPage(initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <ChatPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function typeAndSend(text: string) {
  fireEvent.change(screen.getByLabelText('描述下一步任务'), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: '发送' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  currentEvents = [];
  vi.mocked(conversationsApi.list).mockResolvedValue([makeConversation()]);
  vi.mocked(conversationsApi.create).mockResolvedValue(
    makeConversation({
      id: 'c-new',
      title: '新对话',
      active_run_id: undefined,
    }),
  );
  vi.mocked(conversationsApi.get).mockResolvedValue(makeConversation());
  vi.mocked(conversationsApi.messages).mockResolvedValue([makeMessage()]);
  vi.mocked(runsApi.getPlan).mockResolvedValue({
    run_id: 'run-1',
    status: 'pending',
    plan_markdown: '',
  });
  // 默认:分流为任务(建 run),与流式端点同形状
  vi.mocked(streamMessage).mockImplementation(
    async (_conversationId, _content, _mode, handlers) => {
      handlers.onMeta?.({
        mode: 'run_created',
        message: makeMessage({
          id: 'm9',
          conversation_id: 'c-new',
          run_id: 'run-9',
          content: '把本周邮件整理成周报',
        }),
        run: makeRun({
          id: 'run-9',
          status: 'awaiting_approval',
          conversation_id: 'c-new',
        }),
      });
      handlers.onDone?.({ mode: 'run_created', reply: null });
    },
  );
  vi.mocked(runsApi.get).mockResolvedValue(makeRun());
});

afterEach(() => {
  currentEvents = [];
});

describe('ChatPage', () => {
  it('空态:给出引导且没有内容时不能发送', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { name: '开始一个新任务' }),
    ).toBeTruthy();
    expect(screen.getByText(/描述要完成的事/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.getByLabelText('描述下一步任务')).toHaveValue('');
  });

  it('发送首条消息:先建会话再发消息,消息与状态出现在对话里', async () => {
    renderPage();

    typeAndSend('把本周邮件整理成周报');

    await waitFor(() => expect(conversationsApi.create).toHaveBeenCalled());
    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周邮件整理成周报',
        'auto',
        expect.anything(),
      ),
    );
    // 返回的 run 立即接管:状态从它读
    await waitFor(() => expect(runsApi.get).toHaveBeenCalledWith('run-9'));
    expect((await screen.findAllByText(/待审批/)).length).toBeGreaterThan(0);
  });

  it('在已有会话里发送:不重复建会话,发送走 SSE 流', async () => {
    renderPage('/?conversation=c1');

    await screen.findAllByText('待审批'); // 等 run 载入(在途状态)

    fireEvent.change(screen.getByLabelText('描述下一步任务'), {
      target: { value: '顺便看看日历' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        'c1',
        '顺便看看日历',
        'auto',
        expect.anything(),
      ),
    );
    expect(conversationsApi.create).not.toHaveBeenCalled();
  });

  it('消息历史:user 与 assistant 内容都渲染', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage(),
      makeMessage({
        id: 'a1',
        role: 'assistant',
        content: '# 周报\n\n- 邮件已汇总',
      }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByText('把本周邮件整理成周报')).toBeTruthy();
    expect(
      await screen.findByRole('heading', { name: '周报' }),
    ).toBeInTheDocument();
    expect(screen.getByText('邮件已汇总')).toBeInTheDocument();
  });

  it('待审批:显示放行/拒绝,放行调用 approve', async () => {
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 执行计划',
    });
    renderPage('/?conversation=c1');

    const approveButtons = await screen.findAllByRole('button', {
      name: '放行执行',
    });
    fireEvent.click(approveButtons[0]);

    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认写操作:确认按钮调用 confirm(approve)', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '确认并执行' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith('run-1', 'approve'),
    );
  });

  it('把 run 事件翻成工具卡与状态播报', async () => {
    currentEvents = [
      event(1, 'run.created.v1', { title: '整理本周邮件成周报' }),
      event(2, 'run.planned.v1', { attempt: 1 }),
      event(3, 'run.tool.called.v1', {
        name: 'fetch_emails',
        arguments: '{"limit":5}',
        ok: true,
      }),
      event(4, 'run.delivered.v1', { title: '周报' }),
      event(5, 'run.completed.v1', {}),
    ];
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    renderPage('/?conversation=c1');

    expect((await screen.findAllByText('fetch_emails')).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText(/已交付《周报》/)).toBeTruthy();
    expect(screen.getByText('任务完成 ✅')).toBeTruthy();
  });

  it('失败事件以错误语气展示,并提示去任务编排处理', async () => {
    currentEvents = [
      event(1, 'run.failed.v1', {
        stage: 'execute',
        reason: 'UpstreamError: 上游 500',
      }),
    ];
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'failed', status_reason: 'UpstreamError: 上游 500' }),
    );
    renderPage('/?conversation=c1');

    expect(
      await screen.findByText(/执行失败\(execute\):UpstreamError: 上游 500/),
    ).toBeTruthy();
    expect(
      await screen.findByText('失败原因:UpstreamError: 上游 500'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: '任务编排' })).toHaveAttribute(
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
    renderPage('/?conversation=c1');

    fireEvent.click(
      (await screen.findAllByRole('button', { name: '查看成品' }))[0],
    );

    await waitFor(() => expect(runsApi.artifact).toHaveBeenCalledWith('run-1'));
    expect(
      await screen.findByRole('heading', { name: '本周周报' }),
    ).toBeInTheDocument();
  });

  it('会话选择以 URL 为准:直接打开某个会话就去拉它的详情', async () => {
    vi.mocked(conversationsApi.list).mockResolvedValue([
      makeConversation(),
      makeConversation({
        id: 'c2',
        title: '第二个对话',
        active_run_id: null,
      }),
    ]);
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ id: 'c2', title: '第二个对话', active_run_id: null }),
    );
    renderPage('/?conversation=c2');

    expect(await screen.findByText('第二个对话')).toBeTruthy();
    expect(conversationsApi.get).toHaveBeenCalledWith('c2');
    expect(runsApi.get).not.toHaveBeenCalled(); // 无进行中的 run,不挂状态/事件流
  });

  it('new=1 表示新会话:不选中任何历史会话', () => {
    renderPage('/?new=1');

    expect(
      screen.getByRole('heading', { name: '开始一个新任务' }),
    ).toBeTruthy();
    expect(conversationsApi.get).not.toHaveBeenCalled();
  });

  it('简单对话:只回一条,不建任务、不查状态', async () => {
    vi.mocked(conversationsApi.create).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新对话', active_run_id: null }),
    );
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新对话', active_run_id: null }),
    );
    vi.mocked(streamMessage).mockImplementation(
      async (_conversationId, _content, _mode, handlers) => {
        handlers.onMeta?.({
          mode: 'chat',
          message: makeMessage({ id: 'm2', run_id: null, content: '你好' }),
          run: null,
        });
        handlers.onDelta?.('你好!有什么可以帮你的?');
        handlers.onDone?.({ mode: 'chat', reply: null });
      },
    );
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
    expect(streamMessage).toHaveBeenCalledWith(
      'c-new',
      '你好',
      'auto',
      expect.anything(),
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
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        '你好',
        'chat',
        expect.anything(),
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
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周邮件整理成周报',
        'task',
        expect.anything(),
      ),
    );
  });

  it('流式:增量边到边渲染,不必等整段回复', async () => {
    let releaseGate: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseGate = resolve;
    });
    vi.mocked(streamMessage).mockImplementation(
      async (_conversationId, _content, _mode, handlers) => {
        handlers.onMeta?.({
          mode: 'chat',
          message: makeMessage({ id: 'm2', run_id: null, content: '你好' }),
          run: null,
        });
        handlers.onDelta?.('前半');
        await gate; // 卡住流:验证"此时界面上已经有内容"
        handlers.onDelta?.('后半');
        handlers.onDone?.({ mode: 'chat', reply: null });
      },
    );
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage({ id: 'm2', run_id: null, content: '你好' }),
      makeMessage({
        id: 'a2',
        role: 'assistant',
        run_id: null,
        content: '前半后半',
      }),
    ]);
    renderPage();

    typeAndSend('你好');

    // 流还没结束,界面上已经有内容了——这就是"边输出边显示"
    expect(await screen.findByText('前半')).toBeTruthy();
    await act(async () => {
      releaseGate();
    });
    // 收尾后临时气泡撤掉,由服务端落库的消息接管
    expect(await screen.findByText('前半后半')).toBeTruthy();
    expect(screen.queryByText('前半')).toBeNull();
  });

  it('发送硬失败:内容还给输入框,便于重发', async () => {
    vi.mocked(streamMessage).mockRejectedValue(new Error('网络断了'));
    renderPage();

    typeAndSend('你好');

    expect(await screen.findByText('网络断了')).toBeTruthy();
    expect(screen.getByLabelText('描述下一步任务')).toHaveValue('你好');
  });
});
