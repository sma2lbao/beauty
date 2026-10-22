import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { conversationsApi } from '@/lib/conversations-api';
import type { Conversation } from '@/lib/conversations-api';
import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import { WORKSPACES_KEY } from '@/lib/workspace-selection';

import { ConsoleSidebar } from './sidebar';

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    page: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    messages: vi.fn(),
    postMessage: vi.fn(),
  },
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: { list: vi.fn(async () => []) },
  };
});

vi.mock('@/lib/account-api', () => ({
  accountApi: {
    organization: vi.fn(async () => ({ plan: '团队版' })),
  },
}));

vi.mock('@/lib/auth', () => ({
  // workspace-api 在模块加载时读取 authMode,必须一并提供
  authMode: () => 'token',
  accountLabel: vi.fn(async () => null),
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

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

const otherWorkspace: Workspace = {
  ...workspace,
  id: 'ws-2',
  name: '销售资料',
  description: null,
};

const conversation: Conversation = {
  id: 'c1',
  title: '整理本周日程成周报',
  workspace_id: 'ws-1',
  created_at: '2026-10-13T10:00:00+00:00',
  updated_at: '2026-10-13T10:05:00+00:00',
  archived_at: null,
};

const otherConversation: Conversation = {
  ...conversation,
  id: 'c2',
  title: '销售话术复盘',
  workspace_id: 'ws-2',
};

/** 归属为空的历史会话:应落「未分组」组。 */
const legacyConversation: Conversation = {
  ...conversation,
  id: 'c3',
  title: '未归属的历史会话',
  workspace_id: null,
};

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">
      {location.pathname}
      {location.search}
    </span>
  );
}

type SeededWorkspaces = {
  workspaces: Workspace[];
  defaultWorkspace: Workspace | null;
};

/** seeded 用于直接断言「没有任何工作区」:真实 hook 在清单为空时会回落默认工作区。 */
function renderSidebar(entry = '/?workspace=ws-1', seeded?: SeededWorkspaces) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: seeded ? Infinity : 0 },
    },
  });
  if (seeded) {
    queryClient.setQueryData(WORKSPACES_KEY, seeded);
  }
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ConsoleSidebar />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function lastPageParams() {
  return vi.mocked(conversationsApi.page).mock.lastCall?.[0];
}

/** 会话行(带标题的那个链接)。 */
function sessionLink(title: string) {
  return screen.getByRole('link', { name: new RegExp(title) });
}

/** 「默认工作区」组里第一条会话的操作菜单按钮(每组各有一个同名按钮)。 */
function defaultGroupMenuButton() {
  return within(screen.getByRole('group', { name: '默认工作区' })).getByRole(
    'button',
    { name: '会话操作' },
  );
}

beforeEach(() => {
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([
    workspace,
    otherWorkspace,
  ]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(conversationsApi.page).mockResolvedValue({
    items: [conversation, otherConversation, legacyConversation],
    next_cursor: null,
  });
  vi.mocked(conversationsApi.update).mockResolvedValue(conversation);
  vi.mocked(conversationsApi.remove).mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('ConsoleSidebar 工作区/会话', () => {
  it('取租户全部会话:分页请求不带 workspace_id', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    const params = lastPageParams();
    expect(params?.limit).toBe(20);
    expect(params?.archived).toBe(false);
    expect(params?.workspace_id).toBeUndefined();
  });

  it('按工作区分组:组顺序跟随工作区清单,未分组排最后', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    const groupNames = screen
      .getAllByRole('group')
      .map((group) => group.getAttribute('aria-label'));
    expect(groupNames).toEqual(['默认工作区', '销售资料', '未分组']);

    const defaultGroup = screen.getByRole('group', { name: '默认工作区' });
    expect(
      within(defaultGroup).getByText('整理本周日程成周报'),
    ).toBeInTheDocument();
    expect(within(defaultGroup).getByText('1 个会话')).toBeInTheDocument();
    expect(within(defaultGroup).queryByText('销售话术复盘')).toBeNull();

    const salesGroup = screen.getByRole('group', { name: '销售资料' });
    expect(within(salesGroup).getByText('销售话术复盘')).toBeInTheDocument();

    // 组头标出当前工作区
    expect(screen.getByRole('button', { name: /默认工作区/ })).toHaveAttribute(
      'aria-current',
      'true',
    );
    expect(
      screen.getByRole('button', { name: /销售资料/ }),
    ).not.toHaveAttribute('aria-current');
  });

  it('组头的「+」带该工作区新建会话链接', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    expect(
      screen.getByRole('link', { name: '在「销售资料」中新建会话' }),
    ).toHaveAttribute('href', '/?workspace=ws-2&new=1');
    expect(
      screen.getByRole('link', { name: '在「默认工作区」中新建会话' }),
    ).toHaveAttribute('href', '/?workspace=ws-1&new=1');

    // 未分组组头是纯标题,没有新建入口
    const ungrouped = screen.getByRole('group', { name: '未分组' });
    expect(
      within(ungrouped).queryByRole('link', { name: /新建会话/ }),
    ).toBeNull();
  });

  it('点组头切换到该工作区,保留当前会话', async () => {
    renderSidebar('/?workspace=ws-1&conversation=c1');
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: /销售资料/ }));

    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/?workspace=ws-2&conversation=c1',
      ),
    );
  });

  it('会话行链接带工作区与会话,选中态按 conversation 参数', async () => {
    renderSidebar('/?workspace=ws-1&conversation=c1');
    await screen.findByText('整理本周日程成周报');

    expect(sessionLink('整理本周日程成周报')).toHaveAttribute(
      'href',
      '/?workspace=ws-1&conversation=c1',
    );
    expect(sessionLink('销售话术复盘')).toHaveAttribute(
      'href',
      '/?workspace=ws-2&conversation=c2',
    );
    // 归属为空的历史会话回落到当前工作区
    expect(sessionLink('未归属的历史会话')).toHaveAttribute(
      'href',
      '/?workspace=ws-1&conversation=c3',
    );

    expect(sessionLink('整理本周日程成周报')).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(sessionLink('销售话术复盘')).not.toHaveAttribute('aria-current');
  });

  it('搜索走服务端并改成扁平结果,无匹配时给提示', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [otherConversation],
      next_cursor: null,
    });

    fireEvent.change(screen.getByLabelText('搜索会话'), {
      target: { value: '销售' },
    });

    await waitFor(() =>
      expect(conversationsApi.page).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: '销售', archived: false }),
      ),
    );
    expect(lastPageParams()?.workspace_id).toBeUndefined();
    expect(await screen.findByText('搜索结果')).toBeInTheDocument();
    // 搜索态不再分组
    expect(screen.queryByRole('group')).toBeNull();
    expect(screen.getByText('销售话术复盘')).toBeInTheDocument();

    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    fireEvent.change(screen.getByLabelText('搜索会话'), {
      target: { value: '不存在' },
    });
    expect(await screen.findByText('无匹配会话')).toBeInTheDocument();
  });

  it('已归档切换传参并显示归档空态', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    fireEvent.click(screen.getByRole('button', { name: '已归档' }));

    await waitFor(() =>
      expect(conversationsApi.page).toHaveBeenLastCalledWith(
        expect.objectContaining({ archived: true }),
      ),
    );
    expect(await screen.findByText('暂无已归档会话')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '返回最近' }),
    ).toBeInTheDocument();
  });

  it('没有任何工作区时先引导创建工作区', async () => {
    renderSidebar('/', { workspaces: [], defaultWorkspace: null });

    expect(
      await screen.findByText('还没有工作区,先到工作区页新建一个。'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '创建工作区' })).toHaveAttribute(
      'href',
      '/workspace?new=1',
    );
    expect(screen.getByRole('link', { name: '新会话' })).toHaveAttribute(
      'href',
      '/workspace?new=1',
    );
    // 此时不渲染分组
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('有工作区但没有会话时给空态与开始新会话入口', async () => {
    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    renderSidebar();

    expect(await screen.findByText('暂无会话')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '开始新会话' })).toHaveAttribute(
      'href',
      '/?workspace=ws-1&new=1',
    );
  });

  it('底部入口:新会话、添加工作区与工作区导航', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    expect(screen.getByRole('link', { name: '新会话' })).toHaveAttribute(
      'href',
      '/?workspace=ws-1&new=1',
    );
    expect(screen.getByRole('link', { name: '添加工作区' })).toHaveAttribute(
      'href',
      '/workspace?new=1',
    );
    expect(screen.getByRole('link', { name: '工作区' })).toHaveAttribute(
      'href',
      '/workspace',
    );
  });

  it('游标加载更多:追加下一页会话', async () => {
    vi.mocked(conversationsApi.page)
      .mockResolvedValueOnce({ items: [conversation], next_cursor: 'next-1' })
      .mockResolvedValueOnce({
        items: [otherConversation],
        next_cursor: null,
      });
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));

    expect(await screen.findByText('销售话术复盘')).toBeInTheDocument();
    expect(conversationsApi.page).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'next-1' }),
    );
  });

  it('重命名会话调用 PATCH', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(defaultGroupMenuButton());
    fireEvent.click(screen.getByRole('button', { name: '重命名会话' }));
    const input = screen.getByLabelText('会话标题');
    fireEvent.change(input, { target: { value: '新的周报会话' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        title: '新的周报会话',
      }),
    );
  });

  it('归档与删除会话调用对应接口', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(defaultGroupMenuButton());
    fireEvent.click(screen.getByRole('button', { name: '归档会话' }));
    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        archived: true,
      }),
    );

    fireEvent.click(defaultGroupMenuButton());
    fireEvent.click(screen.getByRole('button', { name: '删除会话' }));
    await waitFor(() =>
      expect(conversationsApi.remove).toHaveBeenCalledWith('c1'),
    );
  });

  it('底部通知入口能就地展开通知面板', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    expect(
      screen.getByRole('dialog', { name: '通知面板' }),
    ).toBeInTheDocument();
  });
});
