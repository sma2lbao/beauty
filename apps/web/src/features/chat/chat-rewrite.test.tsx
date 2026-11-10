import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { conversationsApi, rewriteMessage } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';

import {
  createRenderPage,
  makeMessage,
  setRunEvents,
  setupChatTest,
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
vi.mock('@/features/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

/** 两轮历史:只有最后一轮(m3 user + m4 assistant)该挂操作行。 */
function seedTwoRounds(): void {
  vi.mocked(conversationsApi.messages).mockResolvedValue([
    makeMessage({ id: 'm1', content: '把本周日程整理成周报' }),
    makeMessage({ id: 'm2', role: 'assistant', content: '初版周报' }),
    makeMessage({ id: 'm3', content: '再加一条提醒' }),
    makeMessage({ id: 'm4', role: 'assistant', content: '好,已加上提醒' }),
  ]);
}

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

describe('ChatPage 编辑重发 / 重新生成(interaction-closure §2.2)', () => {
  it('只有最后一轮挂操作行:最后 user 可编辑重发、最后 assistant 可重新生成', async () => {
    seedTwoRounds();
    renderPage('/?conversation=c1');

    expect(await screen.findByText('好,已加上提醒')).toBeInTheDocument();
    // 两轮历史里只有 m3 有编辑重发、m4 有重新生成(历史 user/assistant 不挂操作)
    expect(
      screen.getAllByRole('button', { name: '编辑重发' }),
    ).toHaveLength(1);
    expect(
      screen.getAllByRole('button', { name: '重新生成' }),
    ).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(2);
  });

  it('重新生成:走 rewrite 流(不带 content),并保留原气泡', async () => {
    seedTwoRounds();
    vi.mocked(rewriteMessage).mockResolvedValue(undefined);
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }));

    await waitFor(() =>
      expect(rewriteMessage).toHaveBeenCalledWith(
        'c1',
        'm4',
        expect.any(Object),
        undefined,
      ),
    );
    expect(screen.getByText('好,已加上提醒')).toBeInTheDocument();
  });

  it('编辑重发:就地 textarea 预填原文,确认后带新内容走 rewrite 流', async () => {
    seedTwoRounds();
    vi.mocked(rewriteMessage).mockResolvedValue(undefined);
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '编辑重发' }));
    const textarea = screen.getByLabelText('编辑消息内容');
    expect(textarea).toHaveValue('再加一条提醒');

    fireEvent.change(textarea, { target: { value: '再加两条提醒' } });
    fireEvent.click(screen.getByRole('button', { name: '确认重发' }));

    await waitFor(() =>
      expect(rewriteMessage).toHaveBeenCalledWith(
        'c1',
        'm3',
        expect.any(Object),
        '再加两条提醒',
      ),
    );
    expect(screen.queryByLabelText('编辑消息内容')).toBeNull();
  });

  it('rewrite 失败就地中文提示,不白屏(409 message_not_latest)', async () => {
    seedTwoRounds();
    vi.mocked(rewriteMessage).mockRejectedValue(
      new ApiError(409, 'conversation.message_not_latest', '只能重写最后一轮'),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }));

    expect(
      await screen.findByText('只能重写最后一轮消息,请刷新后再试。'),
    ).toBeInTheDocument();
    // 页面仍完整:历史消息与操作行都在
    expect(screen.getByText('好,已加上提醒')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '重新生成' }),
    ).toBeInTheDocument();
  });
});
