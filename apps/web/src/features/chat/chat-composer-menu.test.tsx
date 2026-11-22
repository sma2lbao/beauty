import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { streamMessage } from '@/lib/conversations-api';
import { skillsApi } from '@/lib/skills-api';

import {
  createRenderPage,
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

function makeSkill() {
  return {
    id: 'coding.repo-readonly-review',
    version: '1.0.0',
    title: '只读代码审查',
    description: '只读仓库并给出审查意见',
    workspace_type: 'local',
    tools: [],
    system_prompt: 'x',
    input_schema: {},
    deliverables: [],
  };
}

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

async function openConversation() {
  renderPage('/?conversation=c1');
  await screen.findAllByText('待审批');
  return screen.getByLabelText('描述下一步任务') as HTMLTextAreaElement;
}

describe('输入区 @ 上下文菜单', () => {
  it('输入 @ 出现技能候选,选中后写回技能并插入可读 token', async () => {
    vi.mocked(skillsApi.list).mockResolvedValue([makeSkill()]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@只读' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    expect(within(menu).getByText('只读代码审查')).toBeInTheDocument();

    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(textarea).toHaveValue('@只读代码审查 '));
    expect(screen.getByLabelText('选择技能')).toHaveValue(
      'coding.repo-readonly-review',
    );
    expect(streamMessage).not.toHaveBeenCalled();
  });

  it('@ 选中的技能随发送请求带 skillId', async () => {
    vi.mocked(skillsApi.list).mockResolvedValue([makeSkill()]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@只读' } });
    await screen.findByRole('listbox', { name: '上下文候选' });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() => expect(textarea).toHaveValue('@只读代码审查 '));

    fireEvent.change(textarea, { target: { value: '用技能汇总' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '用技能汇总',
          skillId: 'coding.repo-readonly-review',
        }),
        expect.anything(),
      ),
    );
  });

  it('@ 没有匹配时仍可直接 Enter 发送', async () => {
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@zzz' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    expect(
      within(menu).getByText('没有匹配的上下文,可直接发送'),
    ).toBeInTheDocument();

    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({ content: '@zzz' }),
        expect.anything(),
      ),
    );
  });
});

describe('输入区 / 命令菜单', () => {
  it('/mode 打开二级菜单,选择后写回发送方式并删除命令文本', async () => {
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '/mode' } });
    const menu = await screen.findByRole('listbox', { name: '命令候选' });
    fireEvent.click(within(menu).getByText('/mode'));

    const submenu = await screen.findByRole('listbox', { name: '命令候选' });
    expect(within(submenu).getByText('选择发送方式')).toBeInTheDocument();
    fireEvent.click(within(submenu).getByText('直接回答'));

    await waitFor(() =>
      expect(screen.getByLabelText('发送方式')).toHaveValue('chat'),
    );
    expect(textarea).toHaveValue('');
    expect(streamMessage).not.toHaveBeenCalled();
  });

  it('/model 菜单打开时 Enter 只展开二级菜单,不发送', async () => {
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '/model' } });
    await screen.findByRole('listbox', { name: '命令候选' });

    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('选择模型')).toBeInTheDocument();
    expect(streamMessage).not.toHaveBeenCalled();
  });
});
