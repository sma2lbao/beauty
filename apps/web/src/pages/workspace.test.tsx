import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import type { ArtifactMeta, Workspace } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';

import { WorkspacePage } from './workspace';

vi.mock('@/lib/workspace-api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/workspace-api')>(
    '@/lib/workspace-api',
  );
  return {
    ...actual,
    workspaceApi: {
      workspaces: vi.fn(),
      defaultWorkspace: vi.fn(),
      createWorkspace: vi.fn(),
      artifacts: vi.fn(),
      artifact: vi.fn(),
      downloadArtifact: vi.fn(),
      removeArtifact: vi.fn(),
    },
  };
});

vi.mock('@/components/runs/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

const artifact: ArtifactMeta = {
  id: 'a1',
  workspace_id: 'ws-1',
  run_id: 'run-1234567890',
  kind: 'markdown',
  title: '周报',
  storage: 'filesystem',
  size_bytes: 61,
  created_at: '2026-10-13T01:00:00+00:00',
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspacePage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([workspace]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(workspaceApi.artifacts).mockResolvedValue([artifact]);
});

describe('WorkspacePage', () => {
  it('展示工作区与交付物元数据(含 run 溯源)', async () => {
    renderPage();

    expect(await screen.findByText('默认工作区')).toBeTruthy();
    expect(await screen.findByText('周报')).toBeTruthy();
    expect(
      screen.getByText(/markdown · filesystem · 61 B · run run-1234…/),
    ).toBeTruthy();
  });

  it('工作区为空时用默认工作区兜底', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText('自动创建:租户默认工作区 · 2026/10/13 08:00:00'),
    ).toBeTruthy();
  });

  it('没有交付物时给出引导文案', async () => {
    vi.mocked(workspaceApi.artifacts).mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText(
        '这个工作区还没有交付物:先在上面建个 run 并批准执行。',
      ),
    ).toBeTruthy();
  });

  it('点「查看」拉取正文并展示', async () => {
    vi.mocked(workspaceApi.artifact).mockResolvedValue({
      ...artifact,
      content: '# 本周周报(成品)\n\n- 邮件已汇总',
    });

    renderPage();
    await screen.findByText('周报');

    fireEvent.click(screen.getByRole('button', { name: '查看' }));

    await waitFor(() => {
      expect(workspaceApi.artifact).toHaveBeenCalledWith('a1');
    });
    expect(
      await screen.findByRole('heading', { name: '本周周报(成品)' }),
    ).toBeTruthy();
    expect(screen.getByText('邮件已汇总')).toBeInTheDocument();
  });

  it('展示后端文件名/MIME,下载走 Blob 端点', async () => {
    vi.mocked(workspaceApi.artifacts).mockResolvedValue([
      {
        ...artifact,
        kind: 'file',
        title: '周报',
        filename: 'weekly-report.md',
        content_type: 'text/markdown',
      },
    ]);
    vi.mocked(workspaceApi.artifact).mockResolvedValue({
      ...artifact,
      filename: 'weekly-report.md',
      content_type: 'text/markdown',
      content: '# 本周周报',
    });
    const blob = new Blob(['# 本周周报'], { type: 'text/markdown' });
    vi.mocked(workspaceApi.downloadArtifact).mockResolvedValue(blob);
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:mock');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
      () => {},
    );

    renderPage();

    expect(await screen.findByText('weekly-report.md')).toBeTruthy();
    expect(screen.getByText(/text\/markdown/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '查看' }));
    await screen.findByRole('heading', { name: '本周周报' });
    fireEvent.click(screen.getByRole('button', { name: '下载' }));

    await waitFor(() => {
      expect(workspaceApi.downloadArtifact).toHaveBeenCalledWith('a1');
    });
    expect(createObjectURL).toHaveBeenCalled();
  });

  it('点「删除」调用删除接口', async () => {
    vi.mocked(workspaceApi.removeArtifact).mockResolvedValue(undefined);

    renderPage();
    await screen.findByText('周报');

    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(workspaceApi.removeArtifact).toHaveBeenCalledWith('a1');
    });
  });

  it('新建工作区后切到该工作区', async () => {
    vi.mocked(workspaceApi.createWorkspace).mockResolvedValue({
      ...workspace,
      id: 'ws-2',
      name: '销售资料',
    });

    renderPage();
    await screen.findByText('默认工作区');

    fireEvent.change(screen.getByLabelText('新建工作区'), {
      target: { value: '销售资料' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => {
      expect(workspaceApi.createWorkspace).toHaveBeenCalledWith({
        name: '销售资料',
        description: null,
      });
    });
    await waitFor(() => {
      expect(workspaceApi.artifacts).toHaveBeenCalledWith('ws-2');
    });
  });

  it('401 时提示先去鉴权', async () => {
    vi.mocked(workspaceApi.workspaces).mockRejectedValue(
      new ApiError(401, 'auth.token_invalid', '令牌无效或已过期'),
    );

    renderPage();

    expect(
      await screen.findByText(/未登录或令牌失效:先在上方面板完成鉴权/),
    ).toBeTruthy();
  });
});
