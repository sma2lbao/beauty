import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { ApiError } from '@/lib/http';
import { conversationsApi } from '@/lib/conversations-api';
import { runsApi } from '@/lib/runs-api';
import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';

import {
  LocationProbe,
  createRenderPage,
  makeConversation,
  setRunEvents,
  setupChatTest,
  workspace,
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

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

describe('ChatPage 页面与 URL', () => {
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
    expect(vi.mocked(runsApi.get)).not.toHaveBeenCalled(); // 无进行中的 run,不挂状态/事件流
  });

  it('new=1 表示新会话:不选中任何历史会话', () => {
    renderPage('/?new=1');

    expect(
      screen.getByRole('heading', { name: '开始一个新会话' }),
    ).toBeTruthy();
    expect(conversationsApi.get).not.toHaveBeenCalled();
  });
});
