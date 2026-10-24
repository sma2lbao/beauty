/**
 * 右列:当前工作区下的会话清单。
 */
import { MessagesSquare } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';

import { SectionHeading } from './section-heading';
import type { WorkspacePageModel } from './use-workspace-page';

export function WorkspaceSessionsPanel({
  model,
}: {
  model: WorkspacePageModel;
}) {
  const {
    activeWorkspace,
    activeWorkspaceId,
    workspaceConversations,
    conversationsLoading,
  } = model;

  return (
    <ConsolePanel className="overflow-hidden">
      <SectionHeading
        icon={<MessagesSquare />}
        title="会话"
        hint={`共 ${workspaceConversations.length} 个`}
      />
      {conversationsLoading ? (
        <p className="px-4 py-4 text-[13px] text-ha-muted">加载中…</p>
      ) : workspaceConversations.length === 0 ? (
        <EmptyPanel
          className="m-3"
          title={`在「${activeWorkspace?.name ?? '工作区'}」里开始第一段会话`}
          description="会话会记录在这里,随时可以从列表继续。"
          action={
            activeWorkspaceId ? (
              <Link
                to={'/?workspace=' + activeWorkspaceId + '&new=1'}
                className={consoleButtonClass({ variant: 'primary', size: 'sm' })}
              >
                新建会话
              </Link>
            ) : null
          }
        />
      ) : (
        <ul className="divide-y divide-ha-border">
          {workspaceConversations.map((conversation) => (
            <li key={conversation.id}>
              <Link
                to={
                  '/?workspace=' +
                  (conversation.workspace_id ?? activeWorkspaceId) +
                  '&conversation=' +
                  conversation.id
                }
                className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-ha-panel-2/60"
              >
                <span className="truncate text-[13px] text-ha-text-body">
                  {conversation.title}
                </span>
                <span className="shrink-0 font-mono text-[11px] text-ha-dim">
                  {formatDateTime(conversation.updated_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </ConsolePanel>
  );
}
