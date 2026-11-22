import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { skillsApi } from '@/lib/skills-api';

import {
  createRenderPage,
  makeConversation,
  makeMessage,
  pickCommandOption,
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
vi.mock('@/lib/connector-api', async () =>
  (await import('./chat-test-harness')).connectorModuleMock(),
);
vi.mock('@/lib/knowledge-api', async () =>
  (await import('./chat-test-harness')).knowledgeModuleMock(),
);

vi.mock('@/lib/memory-api', async () =>
  (await import('./chat-test-harness')).memoryModuleMock(),
);
vi.mock('@/features/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
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
        expect.objectContaining({
          content: '看看附件',
          mode: 'auto',
          attachmentIds: ['a1'],
        }),
        expect.anything(),
      ),
    );
  });

  it('新会话也能选附件:先暂存,建会话后上传并随首条消息发送', async () => {
    const attachment = {
      id: 'a1',
      conversation_id: 'c-new',
      message_id: null,
      filename: 'note.md',
      content_type: 'text/markdown',
      size_bytes: 4,
      created_at: '2026-10-13T10:00:00+00:00',
    };
    vi.mocked(conversationsApi.uploadAttachment).mockResolvedValue(attachment);
    renderPage();
    await screen.findByText('会话将保存在「默认工作区」中。');

    // 新会话没有 id,但附件按钮必须可用(旧实现这里 disabled,点了没反应)
    expect(screen.getByLabelText('添加附件')).toBeEnabled();
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(['# hi'], 'note.md', { type: 'text/markdown' });
    fireEvent.change(input, { target: { files: [file] } });

    // 还没有会话 id:只暂存,不上传
    expect(await screen.findByText('note.md')).toBeInTheDocument();
    expect(conversationsApi.uploadAttachment).not.toHaveBeenCalled();

    typeAndSend('看看附件');
    await waitFor(() =>
      expect(conversationsApi.uploadAttachment).toHaveBeenCalledWith(
        'c-new',
        file,
      ),
    );
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c-new',
        expect.objectContaining({
          content: '看看附件',
          mode: 'auto',
          attachmentIds: ['a1'],
        }),
        expect.anything(),
      ),
    );
  });

  it('暂存附件可移除,发送时不再上传', async () => {
    renderPage();
    await screen.findByText('会话将保存在「默认工作区」中。');
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(['# hi'], 'note.md', { type: 'text/markdown' })],
      },
    });
    await screen.findByText('note.md');

    fireEvent.click(screen.getByLabelText('移除附件 note.md'));
    await waitFor(() => expect(screen.queryByText('note.md')).toBeNull());

    typeAndSend('看看');
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c-new',
        expect.objectContaining({
          content: '看看',
          mode: 'auto',
          attachmentIds: [],
        }),
        expect.anything(),
      ),
    );
    expect(conversationsApi.uploadAttachment).not.toHaveBeenCalled();
  });

  it('超过 10MB 的附件被拒绝并提示', async () => {
    renderPage();
    await screen.findByText('会话将保存在「默认工作区」中。');
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [big] } });

    expect(await screen.findByText('附件超过 10MB 上限')).toBeInTheDocument();
    expect(screen.queryByText('big.pdf')).toBeNull();
    expect(conversationsApi.uploadAttachment).not.toHaveBeenCalled();
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
        system_prompt: 'x',
        input_schema: {},
        deliverables: [],
      },
    ]);
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    // 底部 select 已移除:技能改由 /skill 命令设置
    await pickCommandOption('/skill', '只读代码审查');
    expect(screen.getByLabelText('移除技能 只读代码审查')).toBeInTheDocument();
    typeAndSend('用技能汇总');
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '用技能汇总',
          mode: 'auto',
          skillId: 'coding.repo-readonly-review',
        }),
        expect.anything(),
      ),
    );
  });
});

describe('ChatPage MCP 引用', () => {
  /** 打开 /mcp 二级菜单:server 清单只在这里出现(底部 select 已移除)。 */
  async function openMcpSubmenu() {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    fireEvent.change(screen.getByLabelText('描述下一步任务'), {
      target: { value: '/mcp' },
    });
    const root = await screen.findByRole('listbox', { name: '命令候选' });
    fireEvent.click(within(root).getByText('/mcp'));
    return screen.findByRole('listbox', { name: '命令候选' });
  }

  it('菜单只出现启用的 server,不出现停用的', async () => {
    const submenu = await openMcpSubmenu();

    expect(await within(submenu).findByText('linear')).toBeInTheDocument();
    expect(within(submenu).queryByText('旧 server')).toBeNull();
  });

  it('选择 MCP server 后发送携带 mcp_server_ids', async () => {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');

    await pickCommandOption('/mcp', 'linear');
    expect(screen.getByLabelText('移除 MCP linear')).toBeInTheDocument();
    typeAndSend('查一下 linear 的 issue');

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '查一下 linear 的 issue',
          mode: 'auto',
          mcpServerIds: ['mcp-linear'],
        }),
        expect.anything(),
      ),
    );
  });

  it('URL 的 ?mcp= 作为默认选中', async () => {
    renderPage('/?conversation=c1&mcp=mcp-linear');
    await screen.findAllByText('待审批');

    // 选中态由引用轨能力芯片显示
    expect(await screen.findByLabelText('移除 MCP linear')).toBeInTheDocument();
  });

  it('历史用户消息带 mcp_server_ids 时渲染 MCP chip', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage({ mcp_server_ids: ['mcp-linear'] }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByText(/MCP · linear/)).toBeTruthy();
  });
});

describe('ChatPage 模型路由（C1 §6.3）', () => {
  async function waitForModelOptions() {
    await waitFor(() =>
      expect(screen.getByTestId('chat-model-select').textContent).toContain(
        'gpt-4o-mini',
      ),
    );
  }

  it('已有会话切换模型立即 PATCH,发送时随消息带 model', async () => {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    await waitForModelOptions();

    fireEvent.change(screen.getByTestId('chat-model-select'), {
      target: { value: 'gpt-4o-mini' },
    });

    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        model: 'gpt-4o-mini',
      }),
    );
    // 会话头部小字同步回显,不用等 PATCH 回来
    expect(screen.getByTestId('topbar-model')).toHaveTextContent('gpt-4o-mini');

    typeAndSend('用备用模型再答一次');
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '用备用模型再答一次',
          mode: 'auto',
          model: 'gpt-4o-mini',
        }),
        expect.anything(),
      ),
    );
  });

  it('切回「默认模型」PATCH null', async () => {
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    await waitForModelOptions();

    const select = screen.getByTestId('chat-model-select');
    fireEvent.change(select, { target: { value: 'gpt-4o-mini' } });
    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        model: 'gpt-4o-mini',
      }),
    );

    fireEvent.change(select, { target: { value: '' } });
    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenLastCalledWith('c1', {
        model: null,
      }),
    );
  });

  it('会话已有默认模型时选择器预选并回显到头部', async () => {
    vi.mocked(conversationsApi.get).mockResolvedValue(
      makeConversation({ model: 'gpt-4o-mini' }),
    );
    renderPage('/?conversation=c1');
    await screen.findAllByText('待审批');
    await waitFor(() =>
      expect(screen.getByTestId('chat-model-select')).toHaveValue(
        'gpt-4o-mini',
      ),
    );
    expect(screen.getByTestId('topbar-model')).toHaveTextContent('gpt-4o-mini');
  });

  it('新会话的模型先留在本地,建会话与首条消息都带上', async () => {
    renderPage();
    await screen.findByText('会话将保存在「默认工作区」中。');
    await waitForModelOptions();

    fireEvent.change(screen.getByTestId('chat-model-select'), {
      target: { value: 'gpt-4o-mini' },
    });
    // 新会话没有 id:不 PATCH,等建会话时落库
    expect(conversationsApi.update).not.toHaveBeenCalled();

    typeAndSend('把本周日程整理成周报');
    await waitFor(() =>
      expect(conversationsApi.create).toHaveBeenCalledWith({
        workspace_id: 'ws-1',
        model: 'gpt-4o-mini',
      }),
    );
    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        'c-new',
        expect.objectContaining({
          content: '把本周日程整理成周报',
          mode: 'auto',
          model: 'gpt-4o-mini',
        }),
        expect.anything(),
      ),
    );
  });
});
