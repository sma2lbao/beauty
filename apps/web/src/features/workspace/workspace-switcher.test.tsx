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

import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import { WORKSPACES_KEY } from '@/lib/workspace-selection';

import { WorkspaceSwitcher } from './workspace-switcher';

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

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">
      {location.pathname}
      {location.search}
    </span>
  );
}

function renderSwitcher(
  entry = '/?workspace=ws-1',
  counts: Record<string, number> = { 'ws-1': 3, 'ws-2': 0 },
  seeded?: { workspaces: Workspace[]; defaultWorkspace: Workspace | null },
) {
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
        <WorkspaceSwitcher counts={counts} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([
    workspace,
    otherWorkspace,
  ]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(workspaceApi.renameWorkspace).mockResolvedValue(otherWorkspace);
  vi.mocked(workspaceApi.removeWorkspace).mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('WorkspaceSwitcher', () => {
  it('触发器显示当前工作区,菜单列出全部工作区与会话数', async () => {
    renderSwitcher();
    // 工作区清单是异步的,先等触发器显示出当前工作区名字
    expect(await screen.findByText('默认工作区')).toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: '切换工作区' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);

    const menu = screen.getByRole('menu', { name: '工作区列表' });
    const current = within(menu).getByRole('menuitemradio', {
      name: /默认工作区/,
    });
    expect(current).toHaveAttribute('aria-checked', 'true');
    expect(within(current).getByText('3')).toBeInTheDocument();
    expect(
      within(menu).getByRole('menuitemradio', { name: /销售资料/ }),
    ).toHaveAttribute('aria-checked', 'false');
  });

  it('选另一个工作区只改 workspace 参数并收起菜单', async () => {
    renderSwitcher('/?workspace=ws-1&conversation=c1');
    fireEvent.click(await screen.findByRole('button', { name: '切换工作区' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /销售资料/ }));

    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/?workspace=ws-2',
      ),
    );
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('菜单里的添加入口指向工作区页新建', async () => {
    renderSwitcher();
    fireEvent.click(await screen.findByRole('button', { name: '切换工作区' }));

    expect(
      screen.getByRole('menuitem', { name: '添加工作区…' }),
    ).toHaveAttribute('href', '/workspace?new=1');
  });

  it('管理工作区弹窗:重命名就地提交 PATCH', async () => {
    renderSwitcher();
    fireEvent.click(await screen.findByRole('button', { name: '切换工作区' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '管理工作区…' }));

    const dialog = screen.getByRole('dialog', { name: '管理工作区' });
    expect(within(dialog).getByText('销售资料')).toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole('button', { name: '工作区操作:销售资料' }),
    );
    fireEvent.click(
      within(dialog).getByRole('menuitem', { name: '重命名工作区' }),
    );

    const input = within(dialog).getByLabelText('工作区名称');
    expect(input).toHaveValue('销售资料');
    fireEvent.change(input, { target: { value: '新销售资料' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(workspaceApi.renameWorkspace).toHaveBeenCalledWith('ws-2', {
        name: '新销售资料',
      }),
    );
  });

  it('管理工作区弹窗:删除需就地二次确认', async () => {
    renderSwitcher();
    fireEvent.click(await screen.findByRole('button', { name: '切换工作区' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '管理工作区…' }));

    const dialog = screen.getByRole('dialog', { name: '管理工作区' });
    fireEvent.click(
      within(dialog).getByRole('button', { name: '工作区操作:销售资料' }),
    );
    fireEvent.click(
      within(dialog).getByRole('menuitem', { name: '删除工作区' }),
    );

    expect(
      within(dialog).getByText(/会话会保留并移入「未分组」/),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }));

    await waitFor(() =>
      expect(workspaceApi.removeWorkspace).toHaveBeenCalledWith('ws-2'),
    );
  });

  it('默认工作区不可删除:菜单项禁用并给出说明', async () => {
    renderSwitcher();
    fireEvent.click(await screen.findByRole('button', { name: '切换工作区' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '管理工作区…' }));

    const dialog = screen.getByRole('dialog', { name: '管理工作区' });
    fireEvent.click(
      within(dialog).getByRole('button', { name: '工作区操作:默认工作区' }),
    );

    expect(
      within(dialog).getByRole('menuitem', { name: '删除工作区' }),
    ).toBeDisabled();
    expect(
      within(dialog).getByText('默认工作区不能删除,可重命名'),
    ).toBeInTheDocument();
  });

  it('没有任何工作区时退化为「选择工作区…」入口', async () => {
    renderSwitcher('/', {}, { workspaces: [], defaultWorkspace: null });

    expect(
      await screen.findByRole('link', { name: '选择工作区…' }),
    ).toHaveAttribute('href', '/workspace?new=1');
    expect(screen.queryByRole('button', { name: '切换工作区' })).toBeNull();
  });
});
