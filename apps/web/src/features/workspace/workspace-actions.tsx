/**
 * 工作区操作入口(dsh 风格):组头/清单行右侧「⋯」菜单 → 重命名 / 删除。
 *
 * - 重命名:预填当前名字,Enter 保存,空名禁用;成功即关闭并刷新清单;
 * - 删除:二次确认,文案说明「会话保留并移入未分组、交付物一并删除」;
 *   默认工作区(is_default)菜单项禁用,由系统维护;
 * - 菜单点外部 / Escape 关闭,焦点回到触发按钮。
 */
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@beauty/design';
import { MoreHorizontal } from 'lucide-react';
import { useId, useState } from 'react';

import { ConsoleDialog } from '@/components/console/dialog';
import {
  workspaceErrorMessage,
  useWorkspaceMutations,
} from '@/lib/workspace-mutations';
import type { Workspace } from '@/lib/workspace-api';
import { cn } from '@/lib/utils';

const MENU_ITEM_DISABLED = 'disabled:cursor-not-allowed disabled:opacity-40';

export function WorkspaceActions({
  workspace,
  className,
  triggerClassName,
}: {
  workspace: Workspace;
  className?: string;
  triggerClassName?: string;
}) {
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null);
  const [name, setName] = useState(workspace.name);
  const inputId = useId();
  const { rename, remove } = useWorkspaceMutations();

  const openRename = () => {
    setName(workspace.name);
    rename.reset();
    setDialog('rename');
  };

  const openDelete = () => {
    remove.reset();
    setDialog('delete');
  };

  const closeDialog = () => {
    setDialog(null);
  };

  const trimmedName = name.trim();
  const renameBusy = rename.isPending;
  const deleteBusy = remove.isPending;
  const renameError = rename.error ? workspaceErrorMessage(rename.error) : null;
  const deleteError = remove.error ? workspaceErrorMessage(remove.error) : null;

  const submitRename = async () => {
    if (!trimmedName || renameBusy) {
      return;
    }
    try {
      await rename.mutateAsync({
        workspaceId: workspace.id,
        name: trimmedName,
      });
      setDialog(null);
    } catch {
      // 错误已由 mutation.error 呈现,保持对话框打开便于修改。
    }
  };

  const submitDelete = async () => {
    if (deleteBusy) {
      return;
    }
    try {
      await remove.mutateAsync(workspace.id);
      setDialog(null);
    } catch {
      // 同上。
    }
  };

  return (
    <div className={cn('relative flex shrink-0', className)}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={`工作区操作:${workspace.name}`}
            onClick={(event) => event.stopPropagation()}
            className={cn(
              'inline-grid h-6 w-6 place-items-center rounded-md text-ha-dim transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2',
              triggerClassName,
            )}
          >
            <MoreHorizontal aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          aria-label={`工作区操作:${workspace.name}`}
          align="end"
          size="sm"
          className="w-40"
          onClick={(event) => event.stopPropagation()}
        >
          <DropdownMenuItem
            onSelect={openRename}
            className={cn(
              MENU_ITEM_DISABLED,
              'text-ha-text-2 hover:bg-ha-panel-2',
            )}
          >
            重命名工作区
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={openDelete}
            disabled={workspace.is_default === true}
            title={
              workspace.is_default ? '默认工作区由系统维护,不能删除' : undefined
            }
            className={cn(
              MENU_ITEM_DISABLED,
              workspace.is_default
                ? 'text-ha-dim'
                : 'text-ha-danger hover:bg-ha-danger-bg',
            )}
          >
            删除工作区
          </DropdownMenuItem>
          {workspace.is_default ? (
            <p className="px-2.5 pb-1 pt-1 text-[10px] leading-4 text-ha-faint">
              默认工作区不能删除,可重命名
            </p>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* 重命名 */}
      <ConsoleDialog
        open={dialog === 'rename'}
        title="重命名工作区"
        description="只改显示名称;该工作区里的会话与交付物不受影响。"
        onClose={closeDialog}
        footer={
          <>
            <button
              type="button"
              onClick={closeDialog}
              className="inline-flex h-8 items-center rounded-md border border-ha-border-strong px-3 text-[12px] text-ha-text-2 transition-colors hover:bg-ha-panel-2"
            >
              取消
            </button>
            <button
              type="button"
              onClick={submitRename}
              disabled={!trimmedName || renameBusy}
              className="inline-flex h-8 items-center rounded-md bg-ha-accent px-3 text-[12px] font-medium text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {renameBusy ? '保存中…' : '保存'}
            </button>
          </>
        }
      >
        <label
          htmlFor={inputId}
          className="mb-1.5 block text-[11px] text-ha-muted"
        >
          工作区名称
        </label>
        <input
          id={inputId}
          value={name}
          maxLength={120}
          autoFocus
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void submitRename();
            }
          }}
          className="h-9 w-full rounded-md border border-ha-border-strong bg-ha-code px-2.5 text-[13px] text-ha-text outline-none focus:border-ha-accent-soft"
        />
        {!trimmedName ? (
          <p className="mt-2 text-[11px] text-ha-warning">名称不能为空</p>
        ) : null}
        {renameError ? (
          <p role="alert" className="mt-2 text-[11px] text-ha-danger">
            {renameError}
          </p>
        ) : null}
      </ConsoleDialog>

      {/* 删除确认 */}
      <ConsoleDialog
        open={dialog === 'delete'}
        title="删除工作区"
        description={
          <>
            删除工作区「{workspace.name}」?
            <br />
            其中的会话会保留,并移入「未分组」;工作区里的交付物会一并删除,且不可撤销。
          </>
        }
        onClose={closeDialog}
        footer={
          <>
            <button
              type="button"
              onClick={closeDialog}
              className="inline-flex h-8 items-center rounded-md border border-ha-border-strong px-3 text-[12px] text-ha-text-2 transition-colors hover:bg-ha-panel-2"
            >
              取消
            </button>
            <button
              type="button"
              onClick={submitDelete}
              disabled={deleteBusy || workspace.is_default === true}
              className="inline-flex h-8 items-center rounded-md bg-ha-danger px-3 text-[12px] font-medium text-[#14080b] transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {deleteBusy ? '删除中…' : '删除工作区'}
            </button>
          </>
        }
      >
        {deleteError ? (
          <p role="alert" className="mt-2 text-[11px] text-ha-danger">
            {deleteError}
          </p>
        ) : null}
      </ConsoleDialog>
    </div>
  );
}
