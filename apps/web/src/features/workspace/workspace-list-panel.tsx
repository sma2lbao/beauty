/**
 * 左列:工作区清单 + 行尾操作菜单 + 内联新建表单。
 */
import { FolderKanban } from 'lucide-react';

import { WorkspaceActions } from '@/components/console/workspace-actions';
import { ConsolePanel } from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';
import { cn } from '@/lib/utils';

import { CreateWorkspaceForm } from './create-workspace-form';
import { SectionHeading } from './section-heading';
import type { WorkspacePageModel } from './use-workspace-page';

export function WorkspaceListPanel({ model }: { model: WorkspacePageModel }) {
  const {
    workspaces,
    activeWorkspaceId,
    selectWorkspace,
    setSelectedArtifact,
    formOpen,
  } = model;

  return (
    <ConsolePanel className="flex h-fit flex-col lg:sticky lg:top-0">
      <SectionHeading
        icon={<FolderKanban />}
        title="工作区"
        hint={`共 ${workspaces.length} 个`}
      />
      <ul className="divide-y divide-ha-border">
        {workspaces.map((workspace) => {
          const selected = workspace.id === activeWorkspaceId;
          return (
            <li key={workspace.id} className="group/workspace flex items-center">
              {/* 选中区与操作入口是并列的两个按钮,避免 button 套 button */}
              <button
                type="button"
                aria-current={selected ? 'true' : undefined}
                onClick={() => {
                  selectWorkspace(workspace.id);
                  setSelectedArtifact(null);
                }}
                className={cn(
                  'min-w-0 flex-1 border-l-2 px-3 py-2.5 text-left transition-colors',
                  selected
                    ? 'border-ha-accent-soft/60 bg-ha-accent-soft/20'
                    : 'border-transparent hover:bg-ha-panel-2/60',
                )}
              >
                <span className="block truncate text-[13px] font-medium text-ha-text">
                  {workspace.name}
                </span>
                <span className="mt-1 block truncate text-[11px] text-ha-muted">
                  {workspace.description?.split('\n')[0] ?? '无描述'}
                </span>
                <span className="mt-0.5 block truncate font-mono text-[11px] text-ha-dim">
                  创建于 {formatDateTime(workspace.created_at)}
                </span>
              </button>
              <WorkspaceActions
                workspace={workspace}
                className="mr-2"
                triggerClassName="opacity-0 transition-opacity group-hover/workspace:opacity-100 group-focus-within/workspace:opacity-100 focus-visible:opacity-100"
              />
            </li>
          );
        })}
      </ul>

      {formOpen ? (
        <div className="border-t border-ha-border p-3">
          <p className="mb-2 text-[12px] font-medium text-ha-text-2">
            新建工作区
          </p>
          <CreateWorkspaceForm model={model} />
        </div>
      ) : null}
    </ConsolePanel>
  );
}
