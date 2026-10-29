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
 */
import {
  Check,
  ChevronDown,
  FolderKanban,
  FolderPlus,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router';

import { ConsoleDialog } from '@/components/console/dialog';
import type { Workspace } from '@/lib/workspace-api';
import {
  workspaceErrorMessage,
  useWorkspaceMutations,
} from '@/lib/workspace-mutations';
import { useWorkspaceSelection } from '@/lib/workspace-selection';
import { cn } from '@/lib/utils';

const menuItemClass =
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
              onSelect={() => {
                setOpen(false);
                if (workspace.id !== activeWorkspaceId) {
                  selectWorkspace(workspace.id);
                }
              }}
            />
          ))}

          <div aria-hidden="true" className="my-1 h-px bg-ha-border" />

          <Link
            to="/workspace?new=1"
            role="menuitem"
            onClick={() => setOpen(false)}
            className={cn(
              menuItemClass,
              'text-ha-muted hover:bg-ha-panel-2/70',
            )}
          >
            <FolderPlus aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
            添加工作区…
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setManagerOpen(true);
            }}
            className={cn(
              menuItemClass,
              'text-ha-muted hover:bg-ha-panel-2/70',
            )}
          >
            <Settings aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
            管理工作区…
          </button>

          <div className="mt-1 flex items-center gap-1.5 rounded-md bg-ha-success-bg px-2 py-1.5 text-[10px] leading-4 text-ha-success">
            <ShieldCheck aria-hidden="true" className="h-3 w-3 shrink-0" />
            沙盒边界内 · 越界写入需批准
          </div>
        </div>
      ) : null}

      <WorkspaceManagerDialog
        open={managerOpen}
        counts={counts}
        onClose={() => setManagerOpen(false)}
      />
    </div>
  );
}

/** 弹窗里的一行:默认展示信息 + ⋯ 菜单;编辑/确认删除时就地换成对应表单。 */
type RowMode = 'idle' | 'menu' | 'rename' | 'delete';

function ManagerRow({
  workspace,
  count,
  active,
  onClose,
}: {
  workspace: Workspace;
  count: number;
  /** 是否是当前选中的工作区(徽标「当前」跟着选中的那个走,不跟默认走)。 */
  active: boolean;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<RowMode>('idle');
  const [name, setName] = useState(workspace.name);
  const rowRef = useRef<HTMLLIElement | null>(null);
  const inputId = useId();
  const { rename, remove } = useWorkspaceMutations();
  const isDefault = workspace.is_default === true;

  const trimmed = name.trim();
  const renameError = rename.error ? workspaceErrorMessage(rename.error) : null;
  const deleteError = remove.error ? workspaceErrorMessage(remove.error) : null;

  useEffect(() => {
    if (mode !== 'menu') {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (rowRef.current && !rowRef.current.contains(event.target as Node)) {
        setMode('idle');
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [mode]);

  const submitRename = async () => {
    if (!trimmed || rename.isPending) {
      return;
    }
    try {
      await rename.mutateAsync({ workspaceId: workspace.id, name: trimmed });
      setMode('idle');
    } catch {
      // 错误由 mutation.error 就地呈现,保持编辑态便于修改。
    }
  };

  const submitDelete = async () => {
    if (remove.isPending) {
      return;
    }
    try {
      await remove.mutateAsync(workspace.id);
      setMode('idle');
      onClose();
    } catch {
      // 同上。
    }
  };

  return (
    <li ref={rowRef} className="relative flex items-start gap-2.5 py-2.5">
      <FolderKanban
        aria-hidden="true"
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          isDefault ? 'text-ha-accent-soft' : 'text-ha-muted',
        )}
      />

      {mode === 'rename' ? (
        <form
          className="min-w-0 flex-1"
          onSubmit={(event) => {
            event.preventDefault();
            void submitRename();
          }}
        >
          <label htmlFor={inputId} className="sr-only">
            工作区名称
          </label>
          <input
            id={inputId}
            value={name}
            maxLength={120}
            autoFocus
            onChange={(event) => setName(event.target.value)}
            className="h-8 w-full rounded-md border border-ha-border-strong bg-ha-code px-2 text-[12px] text-ha-text outline-none focus:border-ha-accent-soft"
          />
          {renameError ? (
            <p role="alert" className="mt-1.5 text-[11px] text-ha-danger">
              {renameError}
            </p>
          ) : null}
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="submit"
              disabled={!trimmed || rename.isPending}
              className="inline-flex h-7 items-center rounded-md bg-ha-accent px-2.5 text-[12px] font-medium text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {rename.isPending ? '保存中…' : '保存'}
            </button>
            <button
              type="button"
              onClick={() => {
                setName(workspace.name);
                rename.reset();
                setMode('idle');
              }}
              className="inline-flex h-7 items-center rounded-md border border-ha-border-strong px-2.5 text-[12px] text-ha-text-2 transition-colors hover:bg-ha-panel-2"
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[12px] font-medium text-ha-text">
              {workspace.name}
            </span>
            {active ? (
              <span className="shrink-0 rounded-[4px] bg-ha-accent-soft/15 px-1 py-0.5 text-[10px] text-ha-accent-soft">
                当前
              </span>
            ) : null}
          </div>
          <p className="mt-1 truncate text-[11px] text-ha-muted">
            {workspace.description?.split('\n')[0] ?? '无描述'}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-ha-faint">
            {count} 个会话 · 创建于{' '}
            {new Date(workspace.created_at).toLocaleDateString('zh-CN')}
          </p>

          {mode === 'delete' ? (
            <div className="mt-2 rounded-md border border-ha-danger-border bg-ha-danger-bg px-2 py-1.5">
              <p className="text-[11px] leading-4 text-ha-danger">
                确认删除「{workspace.name}
                」?会话会保留并移入「未分组」,交付物一并删除。
              </p>
              {deleteError ? (
                <p role="alert" className="mt-1 text-[11px] text-ha-danger">
                  {deleteError}
                </p>
              ) : null}
              <div className="mt-1.5 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void submitDelete()}
                  disabled={remove.isPending}
                  className="inline-flex h-7 items-center rounded-md bg-ha-danger px-2.5 text-[11px] font-medium text-[#14080b] transition-colors hover:brightness-110 disabled:opacity-50"
                >
                  {remove.isPending ? '删除中…' : '删除'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    remove.reset();
                    setMode('idle');
                  }}
                  className="inline-flex h-7 items-center rounded-md border border-ha-border-strong px-2.5 text-[11px] text-ha-text-2 transition-colors hover:bg-ha-panel-2"
                >
                  取消
                </button>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {mode !== 'rename' && mode !== 'delete' ? (
        <button
          type="button"
          aria-label={`工作区操作:${workspace.name}`}
          aria-haspopup="menu"
          aria-expanded={mode === 'menu'}
          onClick={() =>
            setMode((value) => (value === 'menu' ? 'idle' : 'menu'))
          }
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-ha-dim transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2"
        >
          <span
            aria-hidden="true"
            className="font-mono text-[13px] leading-none"
          >
            ⋯
          </span>
        </button>
      ) : null}

      {mode === 'menu' ? (
        <div
          role="menu"
          aria-label={`工作区操作:${workspace.name}`}
          className="absolute right-0 top-10 z-10 w-36 rounded-md border border-ha-border-strong bg-ha-panel p-1 shadow-xl"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setName(workspace.name);
              rename.reset();
              setMode('rename');
            }}
            className={cn(menuItemClass, 'text-ha-text-2 hover:bg-ha-panel-2')}
          >
            重命名工作区
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={isDefault}
            title={isDefault ? '默认工作区由系统维护,不能删除' : undefined}
            onClick={() => {
              remove.reset();
              setMode('delete');
            }}
            className={cn(
              menuItemClass,
              isDefault
                ? 'text-ha-dim'
                : 'text-ha-danger hover:bg-ha-danger-bg',
            )}
          >
            删除工作区
          </button>
          {isDefault ? (
            <p className="px-2 pb-1 pt-1 text-[10px] leading-4 text-ha-faint">
              默认工作区不能删除,可重命名
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function WorkspaceManagerDialog({
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
          <ManagerRow
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
