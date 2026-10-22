import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';

import { useWorkspaceSelection } from './workspace-selection';

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

/** 探针:把 hook 的结果与当前 URL 暴露成可断言文本。 */
function Probe() {
  const location = useLocation();
  const { activeWorkspace, activeWorkspaceId, selectWorkspace } =
    useWorkspaceSelection();
  return (
    <div>
      <span data-testid="search">{location.search}</span>
      <span data-testid="active-id">{activeWorkspaceId ?? 'null'}</span>
      <span data-testid="active-name">{activeWorkspace?.name ?? 'null'}</span>
      <button type="button" onClick={() => selectWorkspace('ws-2')}>
        切到 ws-2
      </button>
      <button
        type="button"
        onClick={() => selectWorkspace('ws-2', { keepConversation: true })}
      >
        保留会话切到 ws-2
      </button>
    </div>
  );
}

function renderProbe(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([
    workspace,
    otherWorkspace,
  ]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
});

describe('useWorkspaceSelection', () => {
  it('URL 没有 workspace 时回落到默认工作区并写回地址', async () => {
    renderProbe('/');

    await waitFor(() =>
      expect(screen.getByTestId('search').textContent).toBe('?workspace=ws-1'),
    );
    expect(screen.getByTestId('active-id').textContent).toBe('ws-1');
    expect(screen.getByTestId('active-name').textContent).toBe('默认工作区');
  });

  it('URL 已有 workspace 时以 URL 为准,不被默认值覆盖', async () => {
    renderProbe('/?workspace=ws-2');

    await waitFor(() =>
      expect(screen.getByTestId('active-name').textContent).toBe('销售资料'),
    );
    expect(screen.getByTestId('search').textContent).toBe('?workspace=ws-2');
  });

  it('切换工作区改 URL,并清掉上一工作区的会话参数', async () => {
    renderProbe('/?workspace=ws-1&conversation=c1&new=1');
    await waitFor(() =>
      expect(screen.getByTestId('active-id').textContent).toBe('ws-1'),
    );

    act(() => {
      screen.getByRole('button', { name: '切到 ws-2' }).click();
    });

    await waitFor(() =>
      expect(screen.getByTestId('search').textContent).toBe('?workspace=ws-2'),
    );
    expect(screen.getByTestId('active-id').textContent).toBe('ws-2');
  });

  it('完全没有工作区时(清单与默认都为空)不崩,active 为 null 且不改 URL', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([]);
    vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(
      null as unknown as Workspace,
    );

    renderProbe('/');

    await waitFor(() =>
      expect(screen.getByTestId('active-id').textContent).toBe('null'),
    );
    expect(screen.getByTestId('active-name').textContent).toBe('null');
    expect(screen.getByTestId('search').textContent).toBe('');
  });

  it('keepConversation 时只换工作区,保留会话深链', async () => {
    renderProbe('/?workspace=ws-1&conversation=c1');

    await waitFor(() =>
      expect(screen.getByTestId('active-id').textContent).toBe('ws-1'),
    );

    act(() => {
      screen.getByRole('button', { name: '保留会话切到 ws-2' }).click();
    });

    await waitFor(() =>
      expect(screen.getByTestId('search').textContent).toBe(
        '?workspace=ws-2&conversation=c1',
      ),
    );
  });
});
