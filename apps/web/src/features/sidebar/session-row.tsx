/**
 * 侧边栏单条会话:状态点 + 标题/元信息 + 就地操作菜单(重命名/归档/删除)。
 */
import { Archive, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import { Link } from 'react-router';

import { StatusDot } from '@/components/console/ui';
import type { Conversation } from '@/lib/conversations-api';
import {
  RUN_STATUS_LABELS,
  relativeTime,
  runStatusTone,
} from '@/lib/presentation';
import { cn } from '@/lib/utils';

import type { SidebarModel } from './use-sidebar-model';

export function SessionRow({
  conversation,
  model,
}: {
  conversation: Conversation;
  model: SidebarModel;
}) {
  const {
    latestRunByConversation,
    selectedConversation,
    renaming,
    setRenaming,
    menuFor,
    setMenuFor,
    renameMutation,
    archiveMutation,
    showArchived,
    confirmDelete,
    workspaceHref,
    sessionWorkspaceId,
  } = model;

  const run = latestRunByConversation.get(conversation.id);
  const tone = run ? runStatusTone(run.status) : 'neutral';
  const meta = run
    ? RUN_STATUS_LABELS[run.status] +
      ' · ' +
      relativeTime(conversation.updated_at)
    : relativeTime(conversation.updated_at);
  const selected = conversation.id === selectedConversation;

  return (
    <div
      key={conversation.id}
      role="listitem"
      aria-label={conversation.title}
      className={cn(
        'group relative flex items-center gap-[9px] rounded-md px-2.5 py-[9px] transition-colors',
        selected ? 'bg-[#1c1d1f]' : 'hover:bg-ha-panel-2/60',
      )}
    >
      <StatusDot tone={tone} pulse={run?.status === 'running'} />

      {renaming?.id === conversation.id ? (
        <form
          className="min-w-0 flex-1"
          onSubmit={(event) => {
            event.preventDefault();
            const title = renaming.title.trim();
            if (title) {
              renameMutation.mutate({ id: conversation.id, title });
            }
          }}
        >
          <input
            aria-label="会话标题"
            autoFocus
            value={renaming.title}
            onChange={(event) =>
              setRenaming({
                id: conversation.id,
                title: event.target.value,
              })
            }
            onBlur={() => setRenaming(null)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setRenaming(null);
            }}
            className="h-6 w-full rounded border border-ha-accent bg-ha-panel px-1.5 text-[12px] text-ha-text outline-none"
          />
        </form>
      ) : (
        <Link
          to={workspaceHref(sessionWorkspaceId(conversation), {
            conversation: conversation.id,
          })}
          aria-current={selected ? 'page' : undefined}
          className="min-w-0 flex-1"
        >
          <span
            className={cn(
              'block truncate text-[13px]',
              selected
                ? 'font-medium text-ha-text'
                : 'font-normal text-ha-text-2',
            )}
          >
            {conversation.title}
          </span>
          <span className="mt-[3px] block truncate font-mono text-[11px] text-ha-dim">
            {meta}
          </span>
        </Link>
      )}

      <button
        type="button"
        aria-label="会话操作"
        aria-expanded={menuFor === conversation.id}
        onClick={() =>
          setMenuFor((value) =>
            value === conversation.id ? null : conversation.id,
          )
        }
        className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded text-ha-dim opacity-0 transition-opacity hover:bg-ha-panel-2 hover:text-ha-text group-hover:opacity-100 focus-visible:opacity-100"
      >
        <MoreHorizontal className="h-3.5 w-3.5" />
      </button>

      {menuFor === conversation.id ? (
        <div className="absolute right-1 top-7 z-20 w-32 rounded-md border border-ha-border bg-ha-sidebar p-1 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
          <button
            type="button"
            onClick={() => {
              setMenuFor(null);
              setRenaming({
                id: conversation.id,
                title: conversation.title,
              });
            }}
            className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-text-2 hover:bg-ha-panel-2"
          >
            <Pencil className="h-3 w-3" />
            重命名会话
          </button>
          <button
            type="button"
            onClick={() => {
              setMenuFor(null);
              archiveMutation.mutate({
                id: conversation.id,
                archived: !showArchived,
              });
            }}
            className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-text-2 hover:bg-ha-panel-2"
          >
            <Archive className="h-3 w-3" />
            {showArchived ? '取消归档' : '归档会话'}
          </button>
          <button
            type="button"
            onClick={() => confirmDelete(conversation)}
            className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-danger hover:bg-ha-danger-bg"
          >
            <Trash2 className="h-3 w-3" />
            删除会话
          </button>
        </div>
      ) : null}
    </div>
  );
}
