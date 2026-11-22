import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { connectorApi } from '@/lib/connector-api';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { knowledgeApi } from '@/lib/knowledge-api';
import { skillsApi } from '@/lib/skills-api';

import {
  createRenderPage,
  makeMessage,
  setRunEvents,
  setupChatTest,
} from './chat-test-harness';
import { describeSendError } from './composer/send-errors';

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

function makeKnowledgeDocument() {
  return {
    id: 'k1',
    title: '合同模板',
    source: 'manual',
    size_bytes: 120,
    chunk_count: 3,
    embedding_model: 'local',
    created_at: '2026-10-13T10:00:00+00:00',
  };
}

function makeNotionProvider() {
  return {
    provider: 'notion',
    display_name: 'Notion',
    description: '页面与数据库',
    auth_kind: 'oauth_user',
    scopes: ['read'],
    tools: [{ name: 'notion.search', description: '搜索页面', risk: 'read' }],
    connected: true,
    connection: {
      id: 'conn-2',
      status: 'active',
      account: 'workspace',
      enabled: true,
    },
  };
}

function makeGithubProvider() {
  return {
    provider: 'github',
    display_name: 'GitHub',
    description: '仓库与 issue',
    auth_kind: 'oauth_user',
    scopes: ['repo:read'],
    tools: [
      { name: 'github.search_issues', description: '搜索 issue', risk: 'read' },
    ],
    connected: true,
    connection: {
      id: 'conn-1',
      status: 'active',
      account: 'octocat',
      enabled: true,
    },
  };
}

describe('输入区 @ 内容引用与连接器(P1)', () => {
  it('选知识库文档后插入 token 与引用轨芯片,发送带 context_refs', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([
      makeKnowledgeDocument(),
    ]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@合同' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    fireEvent.click(within(menu).getByText('合同模板'));

    await waitFor(() => expect(textarea).toHaveValue('@合同模板 '));
    expect(screen.getByLabelText('移除引用 合同模板')).toBeInTheDocument();

    fireEvent.change(textarea, {
      target: { value: '看看 @合同模板 这份资料,总结违约条款' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '看看 @合同模板 这份资料,总结违约条款',
          contextRefs: [{ kind: 'knowledge_document', id: 'k1' }],
        }),
        expect.anything(),
      ),
    );
    // 引用随本轮落库,发送成功后引用轨清空
    await waitFor(() =>
      expect(
        screen.queryByLabelText('移除引用 合同模板'),
      ).not.toBeInTheDocument(),
    );
  });

  it('选连接器后引用轨出现能力芯片,发送带 connector_providers', async () => {
    vi.mocked(connectorApi.listProviders).mockResolvedValue([
      makeGithubProvider() as never,
    ]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@Git' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    fireEvent.click(within(menu).getByText('GitHub'));

    await waitFor(() => expect(textarea).toHaveValue('@GitHub '));
    expect(screen.getByLabelText('移除连接器 GitHub')).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: '查一下 open PR' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          content: '查一下 open PR',
          connectorProviders: ['github'],
        }),
        expect.anything(),
      ),
    );
  });

  it('删除引用芯片同时删正文 token;删除连接器芯片只解绑', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([
      makeKnowledgeDocument(),
    ]);
    vi.mocked(connectorApi.listProviders).mockResolvedValue([
      makeGithubProvider() as never,
    ]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@合同' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    fireEvent.click(within(menu).getByText('合同模板'));
    await waitFor(() => expect(textarea).toHaveValue('@合同模板 '));

    fireEvent.click(screen.getByLabelText('移除引用 合同模板'));
    await waitFor(() => expect(textarea).toHaveValue(''));

    fireEvent.change(textarea, { target: { value: '@Git' } });
    const menuAgain = await screen.findByRole('listbox', {
      name: '上下文候选',
    });
    fireEvent.click(within(menuAgain).getByText('GitHub'));
    await waitFor(() => expect(textarea).toHaveValue('@GitHub '));

    fireEvent.click(screen.getByLabelText('移除连接器 GitHub'));
    await waitFor(() =>
      expect(
        screen.queryByLabelText('移除连接器 GitHub'),
      ).not.toBeInTheDocument(),
    );
    // 连接器是能力绑定:正文里已经写下的可读 token 不追删
    expect(textarea).toHaveValue('@GitHub ');
  });

  it('刷新会话后历史消息回显引用与连接器芯片', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage({
        context_refs: [
          { kind: 'knowledge_document', id: 'k1', title: '合同模板' },
        ],
        connector_providers: ['github'],
      }),
    ]);
    vi.mocked(connectorApi.listProviders).mockResolvedValue([
      makeGithubProvider() as never,
    ]);

    renderPage('/?conversation=c1');

    expect(await screen.findByText('合同模板')).toBeInTheDocument();
    expect(await screen.findByText('连接器 · GitHub')).toBeInTheDocument();
  });
});

describe('连接器多选与引用轨收纳(P2)', () => {
  it('/connector 打开 sticky 二级菜单,连续勾选后仍可继续选', async () => {
    vi.mocked(connectorApi.listProviders).mockResolvedValue([
      makeGithubProvider() as never,
      makeNotionProvider() as never,
    ]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '/connector' } });
    const menu = await screen.findByRole('listbox', { name: '命令候选' });
    // 连接器目录是异步查询:等命令可用再点
    await waitFor(() =>
      expect(
        within(menu).getByText('/connector').closest('button'),
      ).not.toHaveAttribute('aria-disabled'),
    );
    fireEvent.click(within(menu).getByText('/connector'));

    const submenu = await screen.findByRole('listbox', { name: '命令候选' });
    expect(within(submenu).getByText('选择连接器(可多选)')).toBeInTheDocument();

    fireEvent.click(within(submenu).getByText('GitHub'));
    // sticky:选完不关闭,可以直接接着选下一个
    expect(
      await screen.findByRole('listbox', { name: '命令候选' }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('移除连接器 GitHub')).toBeInTheDocument();

    fireEvent.click(
      within(screen.getByRole('listbox', { name: '命令候选' })).getByText(
        'Notion',
      ),
    );
    expect(screen.getByLabelText('移除连接器 Notion')).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: '查一下 issue' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({ connectorProviders: ['github', 'notion'] }),
        expect.anything(),
      ),
    );
  });

  it('sticky 二级菜单里 Space 与 Enter 等价,Esc 收起', async () => {
    vi.mocked(connectorApi.listProviders).mockResolvedValue([
      makeGithubProvider() as never,
    ]);
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '/connector' } });
    const menu = await screen.findByRole('listbox', { name: '命令候选' });
    await waitFor(() =>
      expect(
        within(menu).getByText('/connector').closest('button'),
      ).not.toHaveAttribute('aria-disabled'),
    );
    fireEvent.click(within(menu).getByText('/connector'));
    await screen.findByText('选择连接器(可多选)');

    // 默认高亮「不使用连接器」,↓ 到 GitHub 再 Space 勾选
    fireEvent.keyDown(textarea, { key: 'ArrowDown' });
    fireEvent.keyDown(textarea, { key: ' ' });

    expect(
      await screen.findByLabelText('移除连接器 GitHub'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('listbox', { name: '命令候选' }),
    ).toBeInTheDocument();

    fireEvent.keyDown(textarea, { key: 'Escape' });
    await waitFor(() =>
      expect(
        screen.queryByRole('listbox', { name: '命令候选' }),
      ).not.toBeInTheDocument(),
    );
  });

  it('@ 能引用当前会话的历史消息,发送带 message 引用', async () => {
    const textarea = await openConversation();

    fireEvent.change(textarea, { target: { value: '@周报' } });
    const menu = await screen.findByRole('listbox', { name: '上下文候选' });
    fireEvent.click(within(menu).getByText('把本周日程整理成周报'));

    await waitFor(() => expect(textarea).toHaveValue('@把本周日程整理成周报 '));
    expect(
      screen.getByLabelText('移除引用 把本周日程整理成周报'),
    ).toBeInTheDocument();

    fireEvent.change(textarea, {
      target: { value: '@把本周日程整理成周报 接着上一轮继续' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          contextRefs: [{ kind: 'message', id: 'm1' }],
        }),
        expect.anything(),
      ),
    );
  });

  it('引用超过一行时收成 +N,可展开与收起', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue(
      Array.from({ length: 7 }, (_, index) => {
        const number = index + 1;
        return {
          ...makeKnowledgeDocument(),
          id: `k${number}`,
          title: `文档${number}`,
        };
      }),
    );
    const textarea = await openConversation();

    for (let number = 1; number <= 7; number += 1) {
      fireEvent.change(textarea, { target: { value: `@文档${number}` } });
      const menu = await screen.findByRole('listbox', { name: '上下文候选' });
      fireEvent.click(within(menu).getByText(`文档${number}`));
      // 第 7 条起芯片被收进 +N,这里只等 token 写回正文
      await waitFor(() => expect(textarea).toHaveValue(`@文档${number} `));
    }

    // 7 条只露 6 条,其余收进 +1
    expect(screen.queryByLabelText('移除引用 文档7')).not.toBeInTheDocument();
    const more = screen.getByLabelText('展开其余 1 个引用');
    fireEvent.click(more);

    expect(await screen.findByLabelText('移除引用 文档7')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('收起引用'));
    await waitFor(() =>
      expect(screen.queryByLabelText('移除引用 文档7')).not.toBeInTheDocument(),
    );
  });
});

describe('发送失败文案(P1 错误映射)', () => {
  it('按统一错误码给出可操作提示', () => {
    expect(
      describeSendError(
        new ApiError(404, 'context_ref.not_found', 'context ref not found'),
      ),
    ).toBe('引用的资料已被删除,请移除后重发');
    expect(
      describeSendError(
        new ApiError(409, 'context_ref.too_large', 'context refs too large'),
      ),
    ).toBe('引用内容过长,请减少引用后重发');
    expect(
      describeSendError(
        new ApiError(404, 'connector.not_found', 'connector not found'),
      ),
    ).toBe('连接器未连接或没有可用工具,请去连接器页检查');
  });

  it('未知错误保留原始信息,校验类 422 给参数提示', () => {
    expect(describeSendError(new Error('网络异常'))).toBe('网络异常');
    expect(describeSendError(new ApiError(422, 'validation_error', 'x'))).toBe(
      '引用数量过多或参数不合法,请调整后重发',
    );
  });
});
