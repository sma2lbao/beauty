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
      renameWorkspace: vi.fn(),
      removeWorkspace: vi.fn(),
    },
  };
});

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
  is_default: true,
};

const otherWorkspace: Workspace = {
  ...workspace,
  id: 'ws-2',
  name: '销售资料',
  description: null,
  is_default: false,
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

/** 当前工作区列表里某条会话的操作菜单按钮。 */
function sessionMenuButton(title: string) {
  return within(screen.getByRole('listitem', { name: title })).getByRole(
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
  vi.mocked(workspaceApi.renameWorkspace).mockResolvedValue(otherWorkspace);
  vi.mocked(workspaceApi.removeWorkspace).mockResolvedValue(undefined);
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

  it('会话区只列当前工作区的会话,未分组单独一段', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    // 标题标明当前工作区
    expect(screen.getByText('默认工作区 的会话')).toBeInTheDocument();
    const activeList = screen.getByRole('list', {
      name: '默认工作区 的会话',
    });
    expect(
      within(activeList).getByText('整理本周日程成周报'),
    ).toBeInTheDocument();

    // 其他工作区的会话不进入当前工作区列表
    expect(within(activeList).queryByText('销售话术复盘')).toBeNull();
    // 归属为空的历史会话收进「未分组」,仍然可见
    const ungroupedList = screen.getByRole('list', { name: '未分组会话' });
    expect(
      within(ungroupedList).getByText('未归属的历史会话'),
    ).toBeInTheDocument();
  });

  it('下拉菜单切换工作区:只改 workspace 参数', async () => {
    renderSidebar('/?workspace=ws-1&conversation=c1');
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '切换工作区' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /销售资料/ }));

    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/?workspace=ws-2',
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
    // 归属为空的历史会话回落到当前工作区
    expect(sessionLink('未归属的历史会话')).toHaveAttribute(
      'href',
      '/?workspace=ws-1&conversation=c3',
    );

    expect(sessionLink('整理本周日程成周报')).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(sessionLink('未归属的历史会话')).not.toHaveAttribute('aria-current');
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
    // 搜索态是扁平结果,不受当前工作区过滤
    expect(screen.getByRole('list', { name: '搜索结果' })).toBeInTheDocument();
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
    // 切换器退化成「选择工作区…」占位,点击去创建工作区
    expect(screen.getByRole('link', { name: '选择工作区…' })).toHaveAttribute(
      'href',
      '/workspace?new=1',
    );
  });

  it('有工作区但没有会话时给空态与开始新会话入口', async () => {
    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    renderSidebar();

    expect(await screen.findByText('这个工作区还没有会话')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '开始新会话' })).toHaveAttribute(
      'href',
      '/?workspace=ws-1&new=1',
    );
  });

  it('入口:新会话常驻,添加工作区收进切换器菜单', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    expect(screen.getByRole('link', { name: '新会话' })).toHaveAttribute(
      'href',
      '/?workspace=ws-1&new=1',
    );
    // 「添加工作区」不再是常驻入口,而在下拉菜单里
    expect(screen.queryByRole('link', { name: '添加工作区' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '切换工作区' }));
    expect(
      screen.getByRole('menuitem', { name: '添加工作区…' }),
    ).toHaveAttribute('href', '/workspace?new=1');
    expect(screen.getByRole('link', { name: '工作区' })).toHaveAttribute(
      'href',
      '/workspace',
    );
  });

  it('游标加载更多:追加下一页会话', async () => {
    vi.mocked(conversationsApi.page)
      .mockResolvedValueOnce({ items: [conversation], next_cursor: 'next-1' })
      .mockResolvedValueOnce({
        // 第二页同样是当前工作区的会话;别的工作区的会话不进入列表
        items: [{ ...conversation, id: 'c4', title: '第二页的会话' }],
        next_cursor: null,
      });
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));

    expect(await screen.findByText('第二页的会话')).toBeInTheDocument();
    expect(conversationsApi.page).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'next-1' }),
    );
  });

  it('重命名会话调用 PATCH', async () => {
    renderSidebar();
    await screen.findByText('整理本周日程成周报');

    fireEvent.click(sessionMenuButton('整理本周日程成周报'));
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

    fireEvent.click(sessionMenuButton('整理本周日程成周报'));
    fireEvent.click(screen.getByRole('button', { name: '归档会话' }));
    await waitFor(() =>
      expect(conversationsApi.update).toHaveBeenCalledWith('c1', {
        archived: true,
      }),
    );

    fireEvent.click(sessionMenuButton('整理本周日程成周报'));
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
