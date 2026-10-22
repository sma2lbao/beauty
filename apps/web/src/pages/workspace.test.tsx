import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { conversationsApi, type Conversation } from '@/lib/conversations-api';
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
      renameWorkspace: vi.fn(),
      removeWorkspace: vi.fn(),
    },
  };
});

vi.mock('@/lib/conversations-api', () => ({
  conversationsApi: {
    list: vi.fn(),
    page: vi.fn(),
  },
}));

vi.mock('@/components/runs/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  is_default: true,
  created_at: '2026-10-13T00:00:00+00:00',
};

const studiesWorkspace: Workspace = {
  id: 'ws-2',
  name: '研究组',
  description: '行业研究与竞品资料',
  status: 'active',
  created_at: '2026-10-14T00:00:00+00:00',
};

const conversation: Conversation = {
  id: 'c1',
  title: '整理本周日程成周报',
  workspace_id: 'ws-1',
  created_at: '2026-10-13T02:00:00+00:00',
  updated_at: '2026-10-13T02:30:00+00:00',
  archived_at: null,
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

/** 把当前地址渲染出来,供「点菜单不改当前工作区」这类断言使用。 */
function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{location.pathname + location.search}</span>
  );
}

function renderPage(initialEntry = '/workspace', extra?: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <WorkspacePage />
        {extra}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workspaceApi.workspaces).mockResolvedValue([workspace]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(workspaceApi.artifacts).mockResolvedValue([artifact]);
  vi.mocked(conversationsApi.list).mockResolvedValue([]);
});

describe('WorkspacePage', () => {
  it('以「工作区」呈现清单、当前工作区头部与交付物元数据(含 run 溯源)', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '工作区' })).toBeTruthy();
    expect(
      await screen.findByRole('heading', { name: '默认工作区' }),
    ).toBeTruthy();
    // 清单项与右列头部都展示同一个工作区名/描述
    expect(screen.getAllByText('默认工作区').length).toBeGreaterThan(1);
    expect(
      screen.getAllByText('自动创建:租户默认工作区').length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/创建于 2026\/10\/13/).length).toBeGreaterThan(
      0,
    );

    expect(await screen.findByText('周报')).toBeTruthy();
    expect(
      screen.getByText(/markdown · filesystem · 61 B · run run-1234…/),
    ).toBeTruthy();
  });

  it('展示当前工作区的会话并能跳回会话页', async () => {
    vi.mocked(conversationsApi.list).mockResolvedValue([conversation]);

    renderPage();

    expect(
      await screen.findByRole('link', { name: /整理本周日程成周报/ }),
    ).toHaveAttribute('href', '/?workspace=ws-1&conversation=c1');
    await waitFor(() =>
      expect(conversationsApi.list).toHaveBeenCalledWith({
        workspace_id: 'ws-1',
        limit: 20,
      }),
    );
  });

  it('工作区没有会话时给出新建入口', async () => {
    renderPage();

    expect(
      await screen.findByText('在「默认工作区」里开始第一段会话'),
    ).toBeTruthy();
    const links = await screen.findAllByRole('link', { name: '新建会话' });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/?workspace=ws-1&new=1');
    }
  });

  it('没有交付物时给出引导文案', async () => {
    vi.mocked(workspaceApi.artifacts).mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('这个工作区还没有交付物')).toBeTruthy();
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
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

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

  it('新建工作区后切到该工作区的交付物', async () => {
    vi.mocked(workspaceApi.createWorkspace).mockResolvedValue({
      ...workspace,
      id: 'ws-2',
      name: '销售资料',
    });

    renderPage();
    await screen.findByRole('heading', { name: '默认工作区' });

    fireEvent.click(screen.getByRole('button', { name: '新建工作区' }));
    fireEvent.change(screen.getByLabelText(/工作区名称/), {
      target: { value: '销售资料' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建工作区' }));

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

  it('/workspace?new=1 直接展开并聚焦工作区名输入', async () => {
    renderPage('/workspace?new=1');

    const nameInput = await screen.findByLabelText(/工作区名称/);
    await waitFor(() => expect(nameInput).toHaveFocus());
  });

  it('没有任何工作区时整页给空态与创建入口', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([]);
    vi.mocked(workspaceApi.defaultWorkspace).mockRejectedValue(
      new ApiError(404, 'workspace.not_found', '默认工作区不存在'),
    );

    renderPage();

    expect(await screen.findByText('还没有工作区')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '创建第一个工作区' }));
    expect(await screen.findByLabelText(/工作区名称/)).toBeTruthy();
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

  it('工作区行「⋯」菜单展开显示重命名/删除,且不切换当前工作区', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([
      workspace,
      studiesWorkspace,
    ]);

    renderPage('/workspace', <LocationProbe />);
    await screen.findByRole('button', { name: '工作区操作:研究组' });
    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/workspace?workspace=ws-1',
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: '工作区操作:研究组' }));

    expect(screen.getByRole('menu')).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: '重命名工作区' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: '删除工作区' })).toBeTruthy();
    // 菜单是行内操作入口:展开/点开不会把当前工作区切到 ws-2
    expect(screen.getByTestId('location').textContent).toBe(
      '/workspace?workspace=ws-1',
    );
    expect(conversationsApi.list).toHaveBeenCalledWith({
      workspace_id: 'ws-1',
      limit: 20,
    });
  });

  it('重命名工作区:预填当前名字,提交前 trim 并调 PATCH', async () => {
    vi.mocked(workspaceApi.renameWorkspace).mockResolvedValue({
      ...workspace,
      name: '销售资料',
    });

    renderPage();
    await screen.findByRole('heading', { name: '默认工作区' });

    fireEvent.click(
      screen.getByRole('button', { name: '工作区操作:默认工作区' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名工作区' }));

    const input = (await screen.findByLabelText(
      '工作区名称',
    )) as HTMLInputElement;
    expect(input).toHaveValue('默认工作区');

    fireEvent.change(input, { target: { value: '  销售资料  ' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(workspaceApi.renameWorkspace).toHaveBeenCalledWith('ws-1', {
        name: '销售资料',
      }),
    );
  });

  it('删除工作区:确认文案说明会话移入「未分组」,提交调 DELETE', async () => {
    vi.mocked(workspaceApi.workspaces).mockResolvedValue([
      workspace,
      studiesWorkspace,
    ]);
    vi.mocked(workspaceApi.removeWorkspace).mockResolvedValue(undefined);

    renderPage();
    await screen.findByRole('button', { name: '工作区操作:研究组' });

    fireEvent.click(screen.getByRole('button', { name: '工作区操作:研究组' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '删除工作区' }));

    expect(await screen.findByText(/并移入「未分组」/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '删除工作区' }));

    await waitFor(() =>
      expect(workspaceApi.removeWorkspace).toHaveBeenCalledWith('ws-2'),
    );
  });

  it('默认工作区:「删除工作区」禁用并说明不能删除的原因', async () => {
    renderPage();
    await screen.findByRole('button', { name: '工作区操作:默认工作区' });

    fireEvent.click(
      screen.getByRole('button', { name: '工作区操作:默认工作区' }),
    );

    expect(screen.getByRole('menuitem', { name: '删除工作区' })).toBeDisabled();
    expect(screen.getByText('默认工作区不能删除,可重命名')).toBeTruthy();
  });
});
