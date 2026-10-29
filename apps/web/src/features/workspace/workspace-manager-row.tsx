/**
 * 「管理工作区」弹窗里的一行:默认展示工作区信息 + ⋯ 菜单;
 * 重命名(行内输入)/ 删除(就地二次确认)时就地换成对应表单。
 * 默认工作区由系统维护,不可删除但可重命名。
 */
import { FolderKanban } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

import type { Workspace } from '@/lib/workspace-api';
import {
  workspaceErrorMessage,
  useWorkspaceMutations,
} from '@/lib/workspace-mutations';
import { cn } from '@/lib/utils';

import { menuItemClass } from './workspace-switcher-menu';

type RowMode = 'idle' | 'menu' | 'rename' | 'delete';

export function WorkspaceManagerRow({
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
