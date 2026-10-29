/**
 * 侧栏工作区切换器:单行触发器 + 下拉菜单 + 「管理工作区」弹窗。
 *
 * 交互对齐 ardot 设计稿《Harness Agent Web 控制台》01 主控台:
 * - 侧栏只占一行(文件夹图标 + 当前工作区名 + 箭头),点击展开下拉菜单;
 * - 菜单列出全部工作区(会话数 + 当前项对勾),底部是「添加工作区…」与
 *   「管理工作区…」入口,以及沙盒边界提示 —— 切换工作区不再占用侧栏版面;
 * - 切换只改 URL(?workspace=),真源仍是 useWorkspaceSelection,刷新/分享可还原;
 * - 「管理工作区」弹窗里就地重命名/删除(行内编辑,不嵌套对话框);
 *   默认工作区由系统维护,不能删除;
 * - 还没有任何工作区时,触发器退化为「选择工作区…」占位,点击去创建工作区。
 *
 * 本文件只保留触发器与开合状态;菜单/弹窗/弹窗行分别在同域子文件里。
 */
import { ChevronDown, FolderKanban, FolderPlus } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';

import { useWorkspaceSelection } from '@/lib/workspace-selection';
import { cn } from '@/lib/utils';

import { WorkspaceManagerDialog } from './workspace-manager-dialog';
import { WorkspaceSwitcherMenu } from './workspace-switcher-menu';

export function WorkspaceSwitcher({
  counts,
}: {
  /** 每个工作区的会话数(当前已加载的会话里统计,用于菜单展示)。 */
  counts: Record<string, number>;
}) {
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    isLoading,
    selectWorkspace,
  } = useWorkspaceSelection();
  const [open, setOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // 菜单点外部 / Escape 关闭,焦点回到触发器。
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (!isLoading && workspaces.length === 0) {
    // 空态占位:还没有工作区时,触发器直接引到创建工作区(设计稿 05 画板)。
    return (
      <Link
        to="/workspace?new=1"
        className="flex h-9 w-full items-center gap-2 rounded-md border border-dashed border-ha-ghost bg-ha-panel/40 px-2.5 text-left text-[12px] text-ha-muted transition-colors hover:border-ha-border-strong hover:text-ha-text-2"
      >
        <FolderPlus aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">选择工作区…</span>
        <ChevronDown aria-hidden="true" className="h-3 w-3 shrink-0" />
      </Link>
    );
  }

  const handleSelect = (workspaceId: string) => {
    setOpen(false);
    if (workspaceId !== activeWorkspaceId) {
      selectWorkspace(workspaceId);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-label="切换工作区"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          'flex h-9 w-full items-center gap-2 rounded-md border px-2.5 text-left transition-colors',
          open
            ? 'border-ha-accent-soft/40 bg-ha-panel-2'
            : 'border-ha-border-strong bg-ha-panel hover:border-ha-border-strong hover:bg-ha-panel-2/70',
        )}
      >
        <FolderKanban
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 text-ha-accent-soft"
        />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ha-text">
          {activeWorkspace?.name ?? '选择工作区…'}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            'h-3 w-3 shrink-0 text-ha-dim transition-transform',
            open ? 'rotate-180' : '',
          )}
        />
      </button>

      {open ? (
        <WorkspaceSwitcherMenu
          workspaces={workspaces}
          activeWorkspaceId={activeWorkspaceId}
          counts={counts}
          onSelect={handleSelect}
          onClose={() => setOpen(false)}
          onManage={() => {
            setOpen(false);
            setManagerOpen(true);
          }}
        />
      ) : null}

      <WorkspaceManagerDialog
        open={managerOpen}
        counts={counts}
        onClose={() => setManagerOpen(false)}
      />
    </div>
  );
}
