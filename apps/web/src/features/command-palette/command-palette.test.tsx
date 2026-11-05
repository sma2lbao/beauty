import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { conversationsApi, type Conversation } from '@/lib/conversations-api';
import { useUiStore } from '@/stores/ui-store';

import { CommandPalette } from './command-palette';
import { useCommandPaletteShortcut } from './use-command-palette-shortcut';

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: { page: vi.fn() },
}));

// 工作区选择会连带拉工作区接口;这里只关心面板跳转,直接给一个固定工作区,
// 避免测试被无关请求与默认工作区回写 URL 的 effect 干扰。
vi.mock('@/lib/workspace-selection', () => ({
  useWorkspaceSelection: () => ({
    workspaces: [],
    defaultWorkspace: null,
    activeWorkspace: null,
    activeWorkspaceId: 'ws-1',
    isLoading: false,
    error: null,
    selectWorkspace: vi.fn(),
  }),
}));

const conversation: Conversation = {
  id: 'c-1',
  title: '季度复盘',
  workspace_id: 'ws-9',
  created_at: '2026-11-01T00:00:00+00:00',
  updated_at: '2026-11-02T00:00:00+00:00',
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

function ShortcutProbe() {
  useCommandPaletteShortcut();
  return null;
}

function renderPalette() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <CommandPalette />
        <ShortcutProbe />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function getInput() {
  return screen.getByRole('combobox', { name: '搜索会话、页面或动作' });
}

beforeEach(() => {
  useUiStore.setState({ commandPaletteOpen: true, sidebarVisible: true });
  vi.mocked(conversationsApi.page).mockResolvedValue({
    items: [],
    next_cursor: null,
  });
});

afterEach(() => {
  // store 是模块级单例,不重置会让下一条用例直接带着「已打开」的状态起跑。
  useUiStore.setState({ commandPaletteOpen: false });
  vi.clearAllMocks();
});

describe('CommandPalette', () => {
  it('关闭态不渲染', () => {
    useUiStore.setState({ commandPaletteOpen: false });
    renderPalette();

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('按输入过滤页面项,回车跳转对应路由', async () => {
    renderPalette();
    const input = getInput();

    fireEvent.change(input, { target: { value: '知识' } });

    expect(screen.getByRole('option', { name: /知识库/ })).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: /工作台/ }),
    ).not.toBeInTheDocument();
    // 会话搜索返回空,不该把「没有匹配」误判成页面项
    expect(await screen.findByText('没有匹配的会话')).toBeInTheDocument();

    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/knowledge');
    });
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });

  it('会话搜索结果可回车跳转,并带上工作区', async () => {
    vi.mocked(conversationsApi.page).mockResolvedValue({
      items: [conversation],
      next_cursor: null,
    });
    renderPalette();
    const input = getInput();

    fireEvent.change(input, { target: { value: '复盘' } });

    expect(
      await screen.findByRole('option', { name: /季度复盘/ }),
    ).toBeInTheDocument();
    expect(conversationsApi.page).toHaveBeenCalledWith({
      limit: 8,
      q: '复盘',
    });

    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/?conversation=c-1&workspace=ws-9',
      );
    });
  });

  it('↓ 移动选中项,Enter 执行', () => {
    renderPalette();
    const input = getInput();

    expect(screen.getByRole('option', { name: /工作台/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    fireEvent.keyDown(input, { key: 'ArrowDown' });

    expect(screen.getByRole('option', { name: /任务编排/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(input.getAttribute('aria-activedescendant')).toMatch(/-option-1$/);

    fireEvent.keyDown(input, { key: 'Enter' });

    return waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/runs');
      expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    });
  });

  it('Esc 关闭面板并把 store 置回 false', () => {
    renderPalette();

    fireEvent.keyDown(getInput(), { key: 'Escape' });

    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('Ctrl+K / ⌘K 切换面板,焦点在输入框内也能关闭', () => {
    renderPalette();

    // 输入框内的按键会冒泡到 window:不做 target 过滤,才能原地关掉面板
    fireEvent.keyDown(getInput(), { key: 'k', ctrlKey: true });
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(useUiStore.getState().commandPaletteOpen).toBe(true);
    expect(screen.getByRole('combobox')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });
});
