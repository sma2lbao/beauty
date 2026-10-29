/**
 * 工作区切换器的下拉菜单:工作区行(图标 + 会话数 + 当前项对勾)、
 * 「添加工作区…」「管理工作区…」入口,以及底部沙盒边界提示。
 * 只做展示,展开/关闭与选中逻辑留在入口组件 workspace-switcher.tsx。
 */
import {
  Check,
  FolderKanban,
  FolderPlus,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { Link } from 'react-router';

import type { Workspace } from '@/lib/workspace-api';
import { cn } from '@/lib/utils';

/** 菜单项共用的排版类名(切换器菜单与管理弹窗的行内菜单保持一致)。 */
export const menuItemClass =
  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] transition-colors';

/** 菜单里的工作区行:图标 + 名称 + 会话数 + 当前项对勾。 */
function WorkspaceMenuItem({
  workspace,
  active,
  count,
  onSelect,
}: {
  workspace: Workspace;
  active: boolean;
  count: number;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={active}
      onClick={onSelect}
      className={cn(
        menuItemClass,
        active
          ? 'bg-ha-panel-2 text-ha-text'
          : 'text-ha-text-2 hover:bg-ha-panel-2/70',
      )}
    >
      <FolderKanban
        aria-hidden="true"
        className={cn(
          'h-3.5 w-3.5 shrink-0',
          active ? 'text-ha-accent-soft' : 'text-ha-muted',
        )}
      />
      <span className="min-w-0 flex-1 truncate font-medium">
        {workspace.name}
      </span>
      {count > 0 ? (
        <span className="shrink-0 font-mono text-[10px] text-ha-faint">
          {count}
        </span>
      ) : null}
      {active ? (
        <Check
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 text-ha-accent-soft"
        />
      ) : null}
    </button>
  );
}

export function WorkspaceSwitcherMenu({
  workspaces,
  activeWorkspaceId,
  counts,
  onSelect,
  onClose,
  onManage,
}: {
  workspaces: Workspace[];
  activeWorkspaceId: string | null;
  /** 每个工作区的会话数(当前已加载的会话里统计,用于菜单展示)。 */
  counts: Record<string, number>;
  onSelect: (workspaceId: string) => void;
  /** 点「添加工作区…」后收起菜单。 */
  onClose: () => void;
  /** 收起菜单并打开「管理工作区」弹窗。 */
  onManage: () => void;
}) {
  return (
    <div
      role="menu"
      aria-label="工作区列表"
      className="absolute left-0 top-full z-40 mt-1 w-full min-w-[220px] rounded-lg border border-ha-border-strong bg-ha-panel p-1 shadow-[0_18px_44px_rgba(0,0,0,0.55)]"
    >
      {workspaces.map((workspace) => (
        <WorkspaceMenuItem
          key={workspace.id}
          workspace={workspace}
          active={workspace.id === activeWorkspaceId}
          count={counts[workspace.id] ?? 0}
          onSelect={() => onSelect(workspace.id)}
        />
      ))}

      <div aria-hidden="true" className="my-1 h-px bg-ha-border" />

      <Link
        to="/workspace?new=1"
        role="menuitem"
        onClick={onClose}
        className={cn(menuItemClass, 'text-ha-muted hover:bg-ha-panel-2/70')}
      >
        <FolderPlus aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        添加工作区…
      </Link>
      <button
        type="button"
        role="menuitem"
        onClick={onManage}
        className={cn(menuItemClass, 'text-ha-muted hover:bg-ha-panel-2/70')}
      >
        <Settings aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        管理工作区…
      </button>

      <div className="mt-1 flex items-center gap-1.5 rounded-md bg-ha-success-bg px-2 py-1.5 text-[10px] leading-4 text-ha-success">
        <ShieldCheck aria-hidden="true" className="h-3 w-3 shrink-0" />
        沙盒边界内 · 越界写入需批准
      </div>
    </div>
  );
}
