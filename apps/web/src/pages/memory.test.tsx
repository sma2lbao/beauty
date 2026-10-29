import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryPage } from '@/pages/memory';
import { memoryApi, type Memory } from '@/lib/memory-api';
import { workspaceApi, type Workspace } from '@/lib/workspace-api';
import { WORKSPACES_KEY } from '@/lib/workspace-selection';

vi.mock('@/lib/memory-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/memory-api')>(
      '@/lib/memory-api',
    );
  return {
    ...actual,
    memoryApi: {
      list: vi.fn(),
      search: vi.fn(),
      stats: vi.fn(),
      create: vi.fn(),
      remove: vi.fn(),
      purgeExpired: vi.fn(),
    },
  };
});

vi.mock('@/lib/workspace-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/workspace-api')>(
    '@/lib/workspace-api',
  );
  return {
    ...actual,
    workspaceApi: { workspaces: vi.fn(), defaultWorkspace: vi.fn() },
  };
});

vi.mock('@/features/auth/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

function makeMemory(overrides: Partial<Memory> = {}): Memory {
  return {
    id: 'm1',
    workspace_id: null,
    scope: 'tenant',
    kind: 'note',
    content: '默认使用中文回复',
    pinned: false,
    source_type: 'manual',
    source_id: null,
    expires_at: null,
    last_accessed_at: null,
    access_count: 2,
    created_at: '2026-10-20T08:00:00+00:00',
    updated_at: '2026-10-20T08:00:00+00:00',
    ...overrides,
  };
}

function makeWorkspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: 'ws-1',
    name: '产品组',
    description: null,
    status: 'active',
    is_default: true,
    created_at: '2026-10-01T08:00:00+00:00',
    ...overrides,
  };
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPage(client: QueryClient = makeQueryClient()) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(memoryApi.stats).mockResolvedValue({
    total: 0,
    active: 0,
    pinned: 0,
    expired: 0,
  });
  vi.mocked(memoryApi.list).mockResolvedValue([]);
  vi.mocked(memoryApi.search).mockResolvedValue({ query: '', hits: [] });
  vi.mocked(memoryApi.create).mockResolvedValue(makeMemory());
  vi.mocked(memoryApi.remove).mockResolvedValue(undefined);
  vi.mocked(memoryApi.purgeExpired).mockResolvedValue({ purged: 0 });
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(makeWorkspace());
});

describe('MemoryPage', () => {
  it('渲染统计卡与空态', async () => {
    vi.mocked(memoryApi.stats).mockResolvedValue({
      total: 3,
      active: 2,
      pinned: 1,
      expired: 1,
    });

    renderPage();

    expect(screen.getByTestId('auth-bar')).toBeInTheDocument();
    expect(await screen.findByText('记忆总数')).toBeInTheDocument();
    expect(screen.getByText('有效')).toBeInTheDocument();
    expect(screen.getByText('已过期')).toBeInTheDocument();
    expect(await screen.findByText(/还没有记忆/)).toBeInTheDocument();
  });

  it('渲染列表:类型/作用域/置顶徽章与内容', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue([
      makeMemory({ pinned: true, kind: 'preference', content: '先用中文回答' }),
    ]);

    renderPage();

    expect(await screen.findByText('先用中文回答')).toBeInTheDocument();
    const list = screen.getByRole('list');
    expect(within(list).getByText('偏好')).toBeInTheDocument();
    expect(within(list).getByText('租户级')).toBeInTheDocument();
    expect(within(list).getByText('置顶')).toBeInTheDocument();
  });

  it('切换类型过滤后重新查询列表', async () => {
    renderPage();

    await waitFor(() => expect(memoryApi.list).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('类型'), {
      target: { value: 'preference' },
    });

    await waitFor(() => {
      const calls = vi.mocked(memoryApi.list).mock.calls;
      expect(calls.some(([query]) => query?.kind === 'preference')).toBe(true);
    });
  });

  it('切换工作区过滤后按 workspace_id 查询', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([makeWorkspace()]);

    renderPage();

    await screen.findByRole('option', { name: '产品组' });
    fireEvent.change(screen.getByLabelText('工作区'), {
      target: { value: 'ws-1' },
    });

    await waitFor(() => {
      const calls = vi.mocked(memoryApi.list).mock.calls;
      expect(calls.some(([query]) => query?.workspaceId === 'ws-1')).toBe(true);
    });
  });

  it('与侧栏共享 workspaces 缓存时按 WorkspaceList 形状读取', async () => {
    // 回归:侧栏 useWorkspaceSelection 会把 {workspaces, defaultWorkspace}
    // 写进 ['workspaces'] 缓存;页面若当数组用会在渲染时报
    // "workspaces.map is not a function",整页白屏。
    const cached = makeWorkspace({ name: '侧栏缓存工作区' });
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([cached]);
    vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(cached);

    const client = makeQueryClient();
    client.setQueryData(WORKSPACES_KEY, {
      workspaces: [cached],
      defaultWorkspace: cached,
    });

    renderPage(client);

    expect(
      await screen.findByRole('option', { name: '侧栏缓存工作区' }),
    ).toBeInTheDocument();
  });

  it('检索走 /search 并展示命中', async () => {
    vi.mocked(memoryApi.search).mockResolvedValue({
      query: '合同',
      hits: [makeMemory({ id: 'm2', content: '合同模板使用法务版本' })],
    });

    renderPage();

    fireEvent.change(screen.getByLabelText('检索'), {
      target: { value: '合同' },
    });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByText('合同模板使用法务版本')).toBeInTheDocument();
    expect(memoryApi.search).toHaveBeenCalledWith(
      expect.objectContaining({ query: '合同', limit: 20 }),
    );
  });

  it('写入记忆:内容必填,提交后清空表单', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([makeWorkspace()]);

    renderPage();

    const submit = screen.getByRole('button', { name: '写入记忆' });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/记忆内容/), {
      target: { value: '合同模板使用法务版本' },
    });
    fireEvent.change(screen.getByLabelText('记忆类型'), {
      target: { value: 'fact' },
    });
    fireEvent.change(screen.getByLabelText('作用域'), {
      target: { value: 'workspace' },
    });

    const workspaceSelect = screen.getByLabelText(
      '关联工作区',
    ) as HTMLSelectElement;
    await waitFor(() =>
      expect(
        within(workspaceSelect).getByRole('option', { name: '产品组' }),
      ).toBeInTheDocument(),
    );
    fireEvent.change(workspaceSelect, { target: { value: 'ws-1' } });
    fireEvent.change(screen.getByLabelText('有效期(天)'), {
      target: { value: '30' },
    });
    fireEvent.click(screen.getByLabelText('置顶(始终注入)'));
    fireEvent.click(screen.getByRole('button', { name: '写入记忆' }));

    await waitFor(() =>
      expect(memoryApi.create).toHaveBeenCalledWith({
        content: '合同模板使用法务版本',
        kind: 'fact',
        scope: 'workspace',
        workspace_id: 'ws-1',
        pinned: true,
        ttl_days: 30,
      }),
    );
    await waitFor(() =>
      expect(vi.mocked(memoryApi.list).mock.calls.length).toBeGreaterThan(1),
    );
    expect(
      (screen.getByLabelText(/记忆内容/) as HTMLTextAreaElement).value,
    ).toBe('');
  });

  it('删除需要二次确认', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue([makeMemory()]);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '删除' }));
    expect(memoryApi.remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await waitFor(() => expect(memoryApi.remove).toHaveBeenCalledWith('m1'));
  });

  it('清理过期调用 purge 并提示条数', async () => {
    vi.mocked(memoryApi.stats).mockResolvedValue({
      total: 2,
      active: 1,
      pinned: 0,
      expired: 1,
    });
    vi.mocked(memoryApi.purgeExpired).mockResolvedValue({ purged: 1 });

    renderPage();

    const purge = await screen.findByRole('button', { name: '清理过期' });
    await waitFor(() => expect(purge).not.toBeDisabled());
    fireEvent.click(purge);

    expect(await screen.findByText('已清理 1 条过期记忆')).toBeInTheDocument();
    expect(memoryApi.purgeExpired).toHaveBeenCalled();
  });

  it('列表加载失败展示错误文案', async () => {
    vi.mocked(memoryApi.list).mockRejectedValue(new Error('记忆服务不可用'));

    renderPage();

    expect(await screen.findByText('记忆服务不可用')).toBeInTheDocument();
  });
});
