import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { ChatMessage } from '@shared/contract';

import { App } from '@/app';
import {
  installFakeBridge,
  makeSession,
  removeFakeBridge,
  toSummary,
} from '@/test-utils/fake-bridge';

const SESSION = makeSession({ id: 's1', title: '周报会话' });

afterEach(() => {
  removeFakeBridge();
});

describe('工作台', () => {
  it('未选目录时引导「选择本地目录」,点击调用 workspace.choose', async () => {
    const fake = installFakeBridge({
      workspace: null,
      settings: { workspaceRoot: null },
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole('button', { name: '选择本地目录' }),
    );

    await waitFor(() =>
      expect(fake.mocks.workspaceChoose).toHaveBeenCalledTimes(1),
    );
    expect(
      (await screen.findAllByRole('button', { name: '新建会话' })).length,
    ).toBeGreaterThan(0);
  });

  it('有工作区但没有会话时引导新建会话,点击调用 sessions.create', async () => {
    const fake = installFakeBridge();
    render(<App />);

    const buttons = await screen.findAllByRole('button', {
      name: '新建会话',
    });
    fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(fake.mocks.sessionsCreate).toHaveBeenCalledTimes(1),
    );
    expect(await screen.findByLabelText('输入消息')).toBeInTheDocument();
  });

  it('回车发送消息调用 agent.send,运行中可以停止', async () => {
    const fake = installFakeBridge({
      sessions: [toSummary(SESSION)],
      session: SESSION,
    });
    render(<App />);

    fireEvent.click(await screen.findByText('周报会话'));
    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '请读取 README' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(fake.mocks.agentSend).toHaveBeenCalledWith({
        sessionId: 's1',
        text: '请读取 README',
      }),
    );
    expect(await screen.findByText('请读取 README')).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: '停止' }));
    await waitFor(() =>
      expect(fake.mocks.agentCancel).toHaveBeenCalledWith('s1'),
    );

    act(() =>
      fake.emit({ type: 'run.finished', sessionId: 's1', status: 'cancelled' }),
    );
    expect(
      await screen.findByRole('button', { name: '发送' }),
    ).toBeInTheDocument();
  });

  it('assistant.delta 增量渲染到 Agent 回复', async () => {
    const fake = installFakeBridge({
      sessions: [toSummary(SESSION)],
      session: SESSION,
    });
    render(<App />);

    fireEvent.click(await screen.findByText('周报会话'));
    await screen.findByLabelText('输入消息');

    act(() =>
      fake.emit({
        type: 'run.started',
        sessionId: 's1',
      }),
    );
    act(() =>
      fake.emit({
        type: 'assistant.delta',
        sessionId: 's1',
        messageId: 'm1',
        delta: '这是',
      }),
    );
    act(() =>
      fake.emit({
        type: 'assistant.delta',
        sessionId: 's1',
        messageId: 'm1',
        delta: '文件内容',
      }),
    );

    const reply = screen.getByRole('article', { name: 'Agent 回复' });
    expect(within(reply).getByText(/这是文件内容/)).toBeInTheDocument();
  });

  it('工具消息卡片展示工具名与状态点,状态翻转后更新', async () => {
    const fake = installFakeBridge({
      sessions: [toSummary(SESSION)],
      session: SESSION,
    });
    render(<App />);

    fireEvent.click(await screen.findByText('周报会话'));
    await screen.findByLabelText('输入消息');

    const runningTool: ChatMessage = {
      id: 't1',
      role: 'tool',
      toolName: 'read_file',
      toolStatus: 'running',
      content: '读取 README.md',
      createdAt: '2026-10-18T02:06:00.000Z',
    };

    act(() =>
      fake.emit({
        type: 'session.updated',
        session: makeSession({
          ...SESSION,
          messages: [runningTool],
          messageCount: 1,
        }),
      }),
    );

    expect(await screen.findByText('工具 · read_file')).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();

    act(() =>
      fake.emit({
        type: 'session.updated',
        session: makeSession({
          ...SESSION,
          messages: [{ ...runningTool, toolStatus: 'ok', content: '完成' }],
          messageCount: 1,
        }),
      }),
    );

    expect(await screen.findByText('成功')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /工具 · read_file/ }));
    expect(screen.getByText('输出')).toBeInTheDocument();
  });
});
