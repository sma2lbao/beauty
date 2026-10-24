/**
 * 右列顶部:当前工作区的名称/描述/计数与快捷入口。
 */
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { ConsolePanel } from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';

import type { WorkspacePageModel } from './use-workspace-page';

export function WorkspaceOverviewCard({
  model,
}: {
  model: WorkspacePageModel;
}) {
  const {
    activeWorkspace,
    activeWorkspaceId,
    workspaceConversations,
    artifacts,
  } = model;

  return (
    <ConsolePanel className="p-4">
      {activeWorkspace ? (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-semibold text-ha-text">
              {activeWorkspace.name}
            </h2>
            <p className="mt-1 text-[12px] leading-5 text-ha-muted">
              {activeWorkspace.description ?? '无描述'}
            </p>
            <p className="mt-1 font-mono text-[11px] text-ha-dim">
              创建于 {formatDateTime(activeWorkspace.created_at)} · 会话{' '}
              {workspaceConversations.length} 个 · 交付物 {artifacts.length} 个
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {activeWorkspaceId ? (
              <Link
                to={'/?workspace=' + activeWorkspaceId + '&new=1'}
                className={consoleButtonClass({ variant: 'primary', size: 'sm' })}
              >
                新建会话
              </Link>
            ) : null}
            <a
              href="#workspace-artifacts"
              className={consoleButtonClass({
                variant: 'secondary',
                size: 'sm',
              })}
            >
              查看交付物
            </a>
          </div>
        </div>
      ) : (
        <p className="text-[13px] text-ha-muted">
          先在左侧选择或创建一个工作区。
        </p>
      )}
    </ConsolePanel>
  );
}
