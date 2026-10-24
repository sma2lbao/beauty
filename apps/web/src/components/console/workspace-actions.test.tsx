/**
 * W2 R-21:工作区操作入口契约。
 * - 「⋯」菜单打开后提供重命名/删除两项;
 * - 默认工作区(is_default)两项禁用,由系统维护;
 * - 删除弹二次确认,文案说明会话保留、交付物删除。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Workspace } from '@/lib/workspace-api';

import { WorkspaceActions } from './workspace-actions';

// 写操作统一走 workspace-mutations,整层打桩,只验证入口交互。
// 组件会调用 mutation 的 mutateAsync/reset/isPending,补齐 useMutation 返回形状。
const fakeMutation = () => ({
  mutate: vi.fn(),
  mutateAsync: vi.fn(async () => undefined),
  reset: vi.fn(),
  isPending: false,
});
vi.mock('@/lib/workspace-mutations', () => ({
  workspaceErrorMessage: (error: unknown) => String(error),
  useWorkspaceMutations: () => ({ rename: fakeMutation(), remove: fakeMutation() }),
}));

const workspace: Workspace = {
  id: 'ws-1',
  name: '项目工作区',
  description: '',
  status: 'active',
  created_at: '2026-10-01T00:00:00Z',
  is_default: false,
};

function openMenu(overrides: Partial<Workspace> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <WorkspaceActions workspace={{ ...workspace, ...overrides }} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: /操作|更多|⋯/ }));
}

describe('WorkspaceActions', () => {
  it('打开菜单提供重命名与删除两项', () => {
    openMenu();
    expect(screen.getByRole('menuitem', { name: '重命名工作区' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: '删除工作区' })).toBeTruthy();
  });

  it('默认工作区的删除项禁用并给出解释,重命名仍可用', () => {
    openMenu({ is_default: true });
    expect(
      (screen.getByRole('menuitem', { name: '删除工作区' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    // 「默认工作区不能删除,可重命名」的解释文案存在
    expect(screen.getByText(/默认工作区不能删除/)).toBeTruthy();
    expect(
      (screen.getByRole('menuitem', { name: '重命名工作区' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('删除走二次确认弹窗,说明会话保留与交付物删除', () => {
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: '删除工作区' }));
    // 弹窗标题(文案含变量,宽松匹配)
    expect(screen.getByText(/删除工作区「项目工作区」\?/)).toBeTruthy();
    // 影响面说明(文案跨元素,用宽松匹配)
    expect(screen.getByText(/交付物会一并删除/)).toBeTruthy();
    expect(screen.getByText(/未分组/)).toBeTruthy();
    // 确认按钮存在
    expect(screen.getByRole('button', { name: '删除工作区' })).toBeTruthy();
  });
});
