import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { skillsApi } from '@/lib/skills-api';

import {
  createRenderPage,
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
