import { FolderKanban, Plus } from 'lucide-react';

import { ConsoleButton, EmptyPanel, PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/components/runs/auth-bar';
import { CreateWorkspaceForm } from '@/features/workspace/create-workspace-form';
import { useWorkspacePageModel } from '@/features/workspace/use-workspace-page';
import { WorkspaceArtifactsPanel } from '@/features/workspace/workspace-artifacts-panel';
import { WorkspaceListPanel } from '@/features/workspace/workspace-list-panel';
import { WorkspaceOverviewCard } from '@/features/workspace/workspace-overview-card';
import { WorkspacePreviewPanel } from '@/features/workspace/workspace-preview-panel';
import { WorkspaceSessionsPanel } from '@/features/workspace/workspace-sessions-panel';

/**
 * 工作区中心(/workspace):左侧工作区清单 + 内联新建,右侧当前工作区的会话、交付物与预览。
 *
 * 工作区即数据层的 workspace:?workspace=<id> 是唯一选中真源(与侧栏/会话页共用),
 * 会话与交付物都按工作区归属;页面不再用亮色卡片,统一走 --ha-* 控制台语言。
 */
export function WorkspacePage() {
  const model = useWorkspacePageModel();
  const {
    workspacesLoading,
    hasNoWorkspaces,
    formOpen,
    openCreateForm,
    unauthorized,
    refreshWorkspaces,
  } = model;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="工作区"
        subtitle="每个工作区持有自己的会话与交付物,会话必须归属某个工作区"
        actions={
          <ConsoleButton variant="primary" size="sm" onClick={openCreateForm}>
            <Plus className="h-3.5 w-3.5" />
            新建工作区
          </ConsoleButton>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void refreshWorkspaces()} />

        {unauthorized ? (
          <p className="mt-4 rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning">
            未登录或令牌失效:先在上方面板完成鉴权,再查看工作区。
          </p>
        ) : null}

        {workspacesLoading ? (
          <p className="mt-10 text-center text-[13px] text-ha-muted">加载中…</p>
        ) : hasNoWorkspaces ? (
          <div className="mt-6">
            <EmptyPanel
              icon={<FolderKanban className="h-7 w-7" />}
              title="还没有工作区"
              description="工作区用来收拢会话与交付物;先创建一个,再开始第一段会话。"
              action={
                formOpen ? (
                  <div className="w-full max-w-sm pt-1">
                    <CreateWorkspaceForm model={model} />
                  </div>
                ) : (
                  <ConsoleButton variant="primary" onClick={openCreateForm}>
                    创建第一个工作区
                  </ConsoleButton>
                )
              }
            />
          </div>
        ) : (
          <div className="mt-6 grid gap-6 lg:grid-cols-[260px_1fr]">
            {/*
              左列:工作区清单 + 内联新建表单。
              这里不能加 overflow-hidden:行尾「⋯」菜单是绝对定位,面板会把它裁掉。
            */}
            <WorkspaceListPanel model={model} />

            {/* 右列:当前工作区的头部 + 会话 + 交付物 + 预览 */}
            <div className="min-w-0 space-y-6">
              <WorkspaceOverviewCard model={model} />
              <WorkspaceSessionsPanel model={model} />
              <WorkspaceArtifactsPanel model={model} />
              <WorkspacePreviewPanel model={model} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
