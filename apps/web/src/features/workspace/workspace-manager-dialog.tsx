/**
 * 「管理工作区」弹窗:列出全部工作区(每行可就地重命名/删除),
 * 底部给「浏览系统目录并添加为工作区」入口与沙盒范围建议。
 */
import { FolderPlus } from 'lucide-react';
import { Link } from 'react-router';

import { ConsoleDialog } from '@/components/console/dialog';
import { useWorkspaceSelection } from '@/lib/workspace-selection';

import { WorkspaceManagerRow } from './workspace-manager-row';

export function WorkspaceManagerDialog({
  open,
  counts,
  onClose,
}: {
  open: boolean;
  counts: Record<string, number>;
  onClose: () => void;
}) {
  const { workspaces, activeWorkspaceId } = useWorkspaceSelection();

  return (
    <ConsoleDialog
      open={open}
      title="管理工作区"
      description="在这里添加、重命名或移除工作区。日常切换无需打开此窗口 —— 侧边栏的下拉菜单随时可切,Agent 始终只在所选目录内读写与执行命令。"
      onClose={onClose}
      className="max-w-lg"
      footer={
        <>
          <span className="mr-auto text-[11px] text-ha-faint">
            移除工作区只删除这条记录,不会动你的文件
          </span>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 items-center rounded-md bg-ha-accent px-3 text-[12px] font-medium text-white transition-colors hover:brightness-110"
          >
            完成
          </button>
        </>
      }
    >
      <ul className="divide-y divide-ha-border">
        {workspaces.map((workspace) => (
          <WorkspaceManagerRow
            key={workspace.id}
            workspace={workspace}
            count={counts[workspace.id] ?? 0}
            active={workspace.id === activeWorkspaceId}
            onClose={onClose}
          />
        ))}
      </ul>

      <Link
        to="/workspace?new=1"
        onClick={onClose}
        className="mt-3 flex h-9 items-center justify-center gap-1.5 rounded-md border border-dashed border-ha-ghost text-[12px] text-ha-muted transition-colors hover:border-ha-border-strong hover:text-ha-text-2"
      >
        <FolderPlus aria-hidden="true" className="h-3.5 w-3.5" />
        浏览系统目录并添加为工作区
      </Link>

      <p className="mt-3 rounded-md border border-ha-warning-border bg-ha-warning-bg px-2.5 py-2 text-[11px] leading-4 text-ha-warning">
        建议为每个项目单独建一个工作区。不要选择主目录或磁盘根目录 ——
        范围过大会拖慢检索并提升误操作风险。
      </p>
    </ConsoleDialog>
  );
}
