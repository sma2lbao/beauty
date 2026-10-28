import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { runsApi } from '@/lib/runs-api';

import {
  createRenderPage,
  makeConversation,
  makeMessage,
  setRunEvents,
  setupChatTest,
  typeAndSend,
} from './chat-test-harness';

vi.mock('@/lib/conversations-api', async () =>
  (await import('./chat-test-harness')).conversationsModuleMock(),
);
vi.mock('@/lib/skills-api', async () =>
  (await import('./chat-test-harness')).skillsModuleMock(),
);
vi.mock('@/lib/mcp-api', async () =>
  (await import('./chat-test-harness')).mcpModuleMock(),
);
vi.mock('@/lib/workspace-api', async () =>
  (await import('./chat-test-harness')).workspaceModuleMock(),
);
vi.mock('@/lib/runs-api', async () =>
  (await import('./chat-test-harness')).runsModuleMock(),
);
vi.mock('@/lib/models-api', async () =>
  (await import('./chat-test-harness')).modelsModuleMock(),
);
vi.mock('@/components/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

describe('ChatPage 发送与流式', () => {
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
