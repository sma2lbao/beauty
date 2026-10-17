import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import { workspaceApi } from '@/lib/workspace-api';

import { ChangesPage } from './changes';

vi.mock('@/lib/workspace-api', () => ({
  workspaceApi: {
    defaultWorkspace: vi.fn(),
    workspaces: vi.fn(),
    createWorkspace: vi.fn(),
    artifacts: vi.fn(),
    artifact: vi.fn(),
    removeArtifact: vi.fn(),
  },
}));

const workspace = {
  id: 'w1',
  name: '默认工作区',
  description: null,
  status: 'active',
  created_at: '2026-10-13T08:00:00+00:00',
};

const diffArtifact = {
  id: 'a1',
  workspace_id: 'w1',
  run_id: 'run-1234',
  kind: 'diff',
  title: 'src/middleware/session-guard.ts',
  storage: 'filesystem',
  size_bytes: 140,
  created_at: '2026-10-13T10:00:00+00:00',
};

const markdownArtifact = {
  ...diffArtifact,
  id: 'a2',
  kind: 'markdown',
  title: '周报.md',
  size_bytes: 48,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChangesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(workspaceApi.artifacts).mockResolvedValue([]);
});

describe('ChangesPage', () => {
  it('没有产物时给设计过的空态与入口', async () => {
    renderPage();

    expect(await screen.findByText('还没有产物。')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: '去工作台发起任务' }),
    ).toHaveAttribute('href', '/');
  });

  it('列出产物,并对 diff 正文渲染增删统计', async () => {
    vi.mocked(workspaceApi.artifacts).mockResolvedValue([
      diffArtifact,
      markdownArtifact,
    ]);
    vi.mocked(workspaceApi.artifact).mockResolvedValue({
      ...diffArtifact,
      content: [
        '@@ -1,2 +1,2 @@',
        '- const a = 1;',
        '+ const a = resolve();',
        '  return a;',
      ].join('\n'),
    });
    renderPage();

    expect(
      (await screen.findAllByText('src/middleware/session-guard.ts')).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText('周报.md')).toBeInTheDocument();
    expect(await screen.findByText('+1')).toBeInTheDocument();
    expect(screen.getByText('-1')).toBeInTheDocument();
    expect(screen.getByText('+ const a = resolve();')).toBeInTheDocument();
  });

  it('未登录时提示先去登录', async () => {
    vi.mocked(workspaceApi.artifacts).mockRejectedValue(
      new ApiError(401, 'auth.token_missing', '缺少 Bearer token'),
    );
    renderPage();

    expect(await screen.findByText(/未登录或令牌已失效/)).toBeInTheDocument();
  });
});
