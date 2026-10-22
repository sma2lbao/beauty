import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Conversation, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import type { Run, RunEvent } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';

import type { MCPServer } from '@/lib/mcp-api';
import { mcpApi } from '@/lib/mcp-api';
import { skillsApi } from '@/lib/skills-api';

import { ChatPage } from './chat';

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    messages: vi.fn(),
    postMessage: vi.fn(),
    attachments: vi.fn(async () => []),
    uploadAttachment: vi.fn(),
    downloadAttachment: vi.fn(),
  },
  streamMessage: vi.fn(),
}));

vi.mock('@/lib/skills-api', () => ({
  skillsApi: { list: vi.fn(async () => []), get: vi.fn() },
}));

vi.mock('@/lib/mcp-api', () => ({
  mcpApi: { list: vi.fn(async () => []) },
}));

vi.mock('@/lib/workspace-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/workspace-api')>(
    '@/lib/workspace-api',
  );
  return {
    ...actual,
    workspaceApi: {
      workspaces: vi.fn(),
      defaultWorkspace: vi.fn(),
    },
  };
});

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
      artifacts: vi.fn(async () => []),
      downloadArtifact: vi.fn(),
    },
  };
});

// SSE 管道由控制台测试覆盖;这里只测"事件 → 会话"的映射与就地动作
let currentEvents: RunEvent[] = [];
vi.mock('@/components/runs/use-run-events', () => ({
  useRunEvents: () => ({ events: currentEvents, connected: true }),
}));

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    title: '整理本周日程成周报',
    workspace_id: workspace.id,
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
    content: '把本周日程整理成周报',
    created_at: '2026-10-13T10:01:00+00:00',
    ...overrides,
  };
}

function makeMcpServer(overrides: Partial<MCPServer> = {}): MCPServer {
  return {
    id: 'mcp-linear',
    name: 'linear',
    transport: 'http',
    url: 'https://mcp.linear.app/mcp',
    enabled: true,
    auto_approve: false,
    has_token: false,
    token_hint: null,
    command: null,
    args: [],
    runtime: 'node',
    image: null,
    has_env: false,
    env_keys: [],
    tools_allowlist: [],
    tools: [],
    timeout_seconds: 30,
    last_status: 'ok',
    last_checked_at: null,
    last_error: null,
    has_oauth: false,
    oauth_authorized: false,
    oauth_scopes: [],
    oauth_expires_at: null,
    oauth_authorization_server: null,
    oauth_error: null,
    created_at: '2026-10-13T10:00:00+00:00',
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周日程成周报',
    goal: '汇总本周日程,生成周报草稿',
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

/** 把当前地址渲染出来,供「切工作区后 URL 是否保留 new=1」这类断言使用。 */
function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{location.pathname + location.search}</span>
  );
}

function renderPage(initialEntry = '/', extra?: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <ChatPage />
        {extra}
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // queryClient 供需要手动推进缓存(如 run 终态)的用例使用
  return { ...view, queryClient };
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
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([workspace]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(conversationsApi.list).mockResolvedValue([makeConversation()]);
  vi.mocked(conversationsApi.create).mockResolvedValue(
    makeConversation({
      id: 'c-new',
      title: '新会话',
      active_run_id: undefined,
    }),
  );
  vi.mocked(conversationsApi.get).mockResolvedValue(makeConversation());
  vi.mocked(conversationsApi.messages).mockResolvedValue([makeMessage()]);
  // 一个启用 server + 一个停用 server:选择器只应出现前者
  vi.mocked(mcpApi.list).mockResolvedValue([
    makeMcpServer(),
    makeMcpServer({ id: 'mcp-legacy', name: '旧 server', enabled: false }),
  ]);
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
          content: '把本周日程整理成周报',
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
  it('空态:给出引导、标出归属工作区且没有内容时不能发送', async () => {
    renderPage();

    expect(
      screen.getByRole('heading', { name: '开始一个新会话' }),
    ).toBeTruthy();
    expect(screen.getByText(/描述要完成的事/)).toBeTruthy();
    // 会话必须归属工作区:空态直接告诉用户会存到哪
    expect(
      await screen.findByText('会话将保存在「默认工作区」中。'),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.getByLabelText('描述下一步任务')).toHaveValue('');
  });

  it('没有工作区时引导先创建工作区,不渲染空态 hero 与输入区', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([]);
    vi.mocked(workspaceApi.defaultWorkspace).mockRejectedValue(
      new ApiError(404, 'workspace.not_found', '默认工作区不存在'),
    );

    renderPage();

    expect(await screen.findByText('还没有工作区')).toBeTruthy();
    expect(
      screen.getByText('会话必须归属于工作区:先创建一个工作区'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: '去创建工作区' })).toHaveAttribute(
      'href',
      '/workspace?new=1',
    );
    expect(
      screen.queryByRole('heading', { name: '开始一个新会话' }),
    ).toBeNull();
    expect(screen.queryByLabelText('描述下一步任务')).toBeNull();
  });

  it('hero 里切换工作区:地址栏留在新会话,归属跟着切', async () => {
    const anotherWorkspace: Workspace = {
      ...workspace,
      id: 'ws-2',
      name: '研究组',
    };
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([
      workspace,
      anotherWorkspace,
    ]);

    renderPage('/?workspace=ws-1&new=1', <LocationProbe />);

    const selector = (await screen.findByLabelText(
      '选择工作区',
    )) as HTMLSelectElement;
    expect(selector).toHaveValue('ws-1');

    fireEvent.change(selector, { target: { value: 'ws-2' } });

    // 切工作区不能掉回旧会话:conversation 清掉、new=1 保留
    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/?workspace=ws-2&new=1',
      ),
    );
    expect(await screen.findByText('会话将保存在「研究组」中。')).toBeTruthy();
  });

  it('打开属于其它工作区的会话:URL 按会话归属对齐并保留会话', async () => {
    const anotherWorkspace: Workspace = {
      ...workspace,
      id: 'ws-2',
      name: '研究组',
    };
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([
      workspace,
      anotherWorkspace,
    ]);

    renderPage('/?workspace=ws-2&conversation=c1', <LocationProbe />);

    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/?workspace=ws-1&conversation=c1',
      ),
    );
  });

  it('会话指向已删除的工作区时:URL 不被改回那个工作区', async () => {
    const anotherWorkspace: Workspace = {
      ...workspace,
      id: 'ws-2',
      name: '研究组',
    };
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([
      workspace,
      anotherWorkspace,
    ]);
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ workspace_id: 'ws-gone' }),
    );

    renderPage('/?workspace=ws-2&conversation=c1', <LocationProbe />);

    // 等会话正文渲染完,确认「归属对齐」effect 已经跑过
    await screen.findByText('把本周日程整理成周报');
    // 遗留会话留在当前工作区(未分组),地址栏不被改到已删除的 ws-gone
    expect(screen.getByTestId('location').textContent).toBe(
      '/?workspace=ws-2&conversation=c1',
    );
  });

  it('发送首条消息:先建会话再发消息,消息与状态出现在会话里', async () => {
    renderPage();
    // 等工作区解析完成,确保 create 带上 workspace_id
    await screen.findByText('会话将保存在「默认工作区」中。');

    typeAndSend('把本周日程整理成周报');

    await waitFor(() =>
      expect(conversationsApi.create).toHaveBeenCalledWith({
        workspace_id: 'ws-1',
      }),
    );
    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周日程整理成周报',
        'auto',
        expect.anything(),
        [],
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
        [],
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
        content: '# 周报\n\n- 日程已汇总',
      }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByText('把本周日程整理成周报')).toBeTruthy();
    expect(
      await screen.findByRole('heading', { name: '周报' }),
    ).toBeInTheDocument();
    expect(screen.getByText('日程已汇总')).toBeInTheDocument();
  });

  it('待审批:显示放行/拒绝,放行调用 approve', async () => {
    currentEvents = [
      event(1, 'token.delta.v1', { text: '# 执行计划', phase: 'plan' }),
    ];
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 执行计划',
    });
    renderPage('/?conversation=c1');

    const approveButtons = await screen.findAllByRole('button', {
      name: '放行执行',
    });
    // 等审批时 run 并没有在跑:实时正文气泡收起,正文交给审批卡片
    expect(screen.queryByRole('status', { name: '任务实时输出' })).toBeNull();
    fireEvent.click(approveButtons[0]);

    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认危险操作:确认按钮调用 confirm(approve)', async () => {
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
      event(1, 'run.created.v1', { title: '整理本周日程成周报' }),
      event(2, 'run.planned.v1', { attempt: 1 }),
      event(3, 'run.tool.called.v1', {
        name: 'search_knowledge',
        arguments: '{"query":"本周日程"}',
        ok: true,
      }),
      event(4, 'run.delivered.v1', { title: '周报' }),
      event(5, 'run.completed.v1', {}),
    ];
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    renderPage('/?conversation=c1');

    expect(
      (await screen.findAllByText('search_knowledge')).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/已交付《周报》/)).toBeTruthy();
    expect(screen.getByText('任务完成 ✅')).toBeTruthy();
  });

  it('任务执行时按 token.delta 渲染实时正文,run 终态后让位给落库消息', async () => {
    currentEvents = [
      event(1, 'token.delta.v1', { text: '正在汇总', phase: 'execute' }),
      event(2, 'token.delta.v1', { text: '本周日程…', phase: 'execute' }),
    ];
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'running' }));
    const { queryClient } = renderPage('/?conversation=c1');

    // 任务模式收不到 delta 帧,正文只能来自 run 事件;阶段标签跟着 phase 走
    const live = await screen.findByRole('status', {
      name: '任务实时输出',
    });
    expect(within(live).getByText('正在汇总本周日程…')).toBeTruthy();
    expect(within(live).getByText('执行中')).toBeTruthy();

    // run 进入终态:落库的 assistant 消息经 invalidate 出现,实时气泡必须消失,正文不重复
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage(),
      makeMessage({
        id: 'm2',
        role: 'assistant',
        content: '本周日程已汇总成周报',
      }),
    ]);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['run', 'run-1'] });
    });

    expect(await screen.findByText('本周日程已汇总成周报')).toBeTruthy();
    expect(screen.queryByRole('status', { name: '任务实时输出' })).toBeNull();
    expect(screen.queryByText('正在汇总本周日程…')).toBeNull();
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

  it('完成后按需列出全部交付物文件', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(runsApi.artifacts).mockResolvedValue([
      {
        id: 'a1',
        run_id: 'run-1',
        kind: 'markdown',
        title: '周报',
        filename: '本周周报.md',
        content_type: 'text/markdown',
        size_bytes: 120,
        source: 'report',
        created_at: '2026-10-13T10:05:00+00:00',
      },
      {
        id: 'a2',
        run_id: 'run-1',
        kind: 'pdf',
        title: 'report.pdf',
        filename: 'report.pdf',
        content_type: 'application/pdf',
        size_bytes: 2048,
        source: 'mcp',
        created_at: '2026-10-13T10:05:01+00:00',
      },
    ]);
    renderPage('/?conversation=c1');

    fireEvent.click(
      (await screen.findAllByRole('button', { name: '查看成品' }))[0],
    );

    await waitFor(() =>
      expect(runsApi.artifacts).toHaveBeenCalledWith('run-1'),
    );
    expect(await screen.findByText('本周周报.md')).toBeInTheDocument();
    expect(await screen.findByText('report.pdf')).toBeInTheDocument();
  });

  it('会话选择以 URL 为准:直接打开某个会话就去拉它的详情', async () => {
    vi.mocked(conversationsApi.list).mockResolvedValue([
      makeConversation(),
      makeConversation({
        id: 'c2',
        title: '第二个会话',
        active_run_id: null,
      }),
    ]);
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ id: 'c2', title: '第二个会话', active_run_id: null }),
    );
    renderPage('/?conversation=c2');

    expect(await screen.findByText('第二个会话')).toBeTruthy();
    expect(conversationsApi.get).toHaveBeenCalledWith('c2');
    expect(runsApi.get).not.toHaveBeenCalled(); // 无进行中的 run,不挂状态/事件流
  });

  it('new=1 表示新会话:不选中任何历史会话', () => {
    renderPage('/?new=1');

    expect(
      screen.getByRole('heading', { name: '开始一个新会话' }),
    ).toBeTruthy();
    expect(conversationsApi.get).not.toHaveBeenCalled();
  });

  it('简单会话:只回一条,不建任务、不查状态', async () => {
    vi.mocked(conversationsApi.create).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新会话', active_run_id: null }),
    );
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ id: 'c-new', title: '新会话', active_run_id: null }),
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
      [],
    );
    // 没有 run:不查状态、不显示任务状态
    expect(runsApi.get).not.toHaveBeenCalled();
    expect(screen.queryByText('待审批')).toBeNull();
  });

  it('发送方式选「直接回答」时按 chat 发送', async () => {
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
        [],
      ),
    );
  });

  it('发送方式选「任务」时按 task 发送', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('发送方式'), {
      target: { value: 'task' },
    });
    typeAndSend('把本周日程整理成周报');

    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        '把本周日程整理成周报',
        'task',
        expect.anything(),
        [],
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

describe('ChatPage 附件', () => {
  it('既有会话可上传附件,发送时携带 attachment_ids', async () => {
    const attachment = {
      id: 'a1',
      conversation_id: 'c1',
      message_id: null,
      filename: 'note.md',
      content_type: 'text/markdown',
      size_bytes: 4,
      created_at: '2026-10-13T10:00:00+00:00',
    };
    vi.mocked(conversationsApi.attachments).mockResolvedValue([]);
    vi.mocked(conversationsApi.uploadAttachment).mockResolvedValue(attachment);
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');

    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(['# hi'], 'note.md', { type: 'text/markdown' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(conversationsApi.uploadAttachment).toHaveBeenCalledWith(
        'c1',
        file,
      ),
    );
    expect(await screen.findByText('note.md')).toBeInTheDocument();

    typeAndSend('看看附件');
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        '看看附件',
        'auto',
        expect.anything(),
        ['a1'],
      ),
    );
  });
});

describe('ChatPage 技能', () => {
  it('选择技能后发送携带 skill_id', async () => {
    vi.mocked(skillsApi.list).mockResolvedValue([
      {
        id: 'coding.repo-readonly-review',
        version: '1.0.0',
        title: '只读代码审查',
        description: 'x',
        workspace_type: 'local',
        tools: [],
        permissions: {},
        system_prompt: 'x',
        input_schema: {},
        deliverables: [],
        triggers: [],
      },
    ]);
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    fireEvent.change(screen.getByLabelText('选择技能'), {
      target: { value: 'coding.repo-readonly-review' },
    });
    typeAndSend('用技能汇总');
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        '用技能汇总',
        'auto',
        expect.anything(),
        [],
        'coding.repo-readonly-review',
      ),
    );
  });
});

describe('ChatPage MCP 引用', () => {
  async function waitForMcpOptions() {
    await waitFor(() =>
      expect(screen.getByLabelText('选择 MCP server').textContent).toContain(
        'linear',
      ),
    );
  }

  it('选择器只出现启用的 server,不出现停用的', async () => {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    await waitForMcpOptions();

    const select = screen.getByLabelText('选择 MCP server');
    const labels = [...(select as HTMLSelectElement).options].map(
      (option) => option.textContent,
    );
    expect(labels).toContain('linear');
    expect(labels).not.toContain('旧 server');
  });

  it('选择 MCP server 后发送携带 mcp_server_ids', async () => {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    await waitForMcpOptions();

    fireEvent.change(screen.getByLabelText('选择 MCP server'), {
      target: { value: 'mcp-linear' },
    });
    typeAndSend('查一下 linear 的 issue');

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        '查一下 linear 的 issue',
        'auto',
        expect.anything(),
        [],
        undefined,
        ['mcp-linear'],
      ),
    );
  });

  it('URL 的 ?mcp= 作为默认选中', async () => {
    renderPage('/?conversation=c1&mcp=mcp-linear');
    await screen.findAllByText('待审批');
    await waitForMcpOptions();

    await waitFor(() =>
      expect(screen.getByLabelText('选择 MCP server')).toHaveValue(
        'mcp-linear',
      ),
    );
  });

  it('历史用户消息带 mcp_server_ids 时渲染 MCP chip', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage({ mcp_server_ids: ['mcp-linear'] }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByText(/MCP · linear/)).toBeTruthy();
  });
});
