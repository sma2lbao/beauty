/**
 * 侧边栏会话区:搜索框 + 视图标题 + 虚拟列表 + 加载更多。
 *
 * 会话按游标分页累积,量会随使用增长;列表交给 @beauty/design 的 VirtualList,
 * 只渲染视口附近的行,DOM 数量与会话总数解耦。分组标题作为虚拟列表里的一类
 * 条目参与排版,因此「未分组」也留在同一个滚动窗口里。
 */
import { VirtualList } from '@beauty/design';
import { FolderPlus, Plus, Search } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import type { Conversation } from '@/lib/conversations-api';
import { cn } from '@/lib/utils';

import { SessionRow } from './session-row';
import type { SidebarModel } from './use-sidebar-model';

/** 会话行高:与 SessionRow 的固定高度一致,虚拟列表按此定位。 */
const SESSION_ROW_HEIGHT = 52;
/** 「未分组」分组标题的行高。 */
const SECTION_ROW_HEIGHT = 32;

type SessionListItem =
  | { kind: 'section'; key: string; label: string }
  | { kind: 'session'; key: string; conversation: Conversation };

/** 模块级函数:VirtualList 依赖引用稳定,避免滚动时重算全部偏移。 */
function sessionItemHeight(item: SessionListItem) {
  return item.kind === 'section' ? SECTION_ROW_HEIGHT : SESSION_ROW_HEIGHT;
}

function sessionItemKey(item: SessionListItem) {
  return item.key;
}

function sessionItems(
  conversations: Conversation[],
  scope: string,
): SessionListItem[] {
  return conversations.map((conversation) => ({
    kind: 'session',
    key: scope + ':' + conversation.id,
    conversation,
  }));
}

export function SessionList({ model }: { model: SidebarModel }) {
  const {
    query,
    setQuery,
    searching,
    showArchived,
    toggleArchived,
    activeWorkspace,
    activeSessions,
    ungroupedSessions,
    conversations,
    workspaces,
    conversationsQuery,
    listLoading,
    listFailed,
    newSessionHref,
  } = model;

  const entries: SessionListItem[] = searching
    ? sessionItems(conversations, 'search')
    : showArchived
      ? sessionItems(activeSessions, 'archived')
      : [
          ...sessionItems(activeSessions, 'active'),
          ...(ungroupedSessions.length > 0
            ? [
                {
                  kind: 'section' as const,
                  key: 'section:ungrouped',
                  label: '未分组',
                },
                ...sessionItems(ungroupedSessions, 'ungrouped'),
              ]
            : []),
        ];

  const listLabel = searching
    ? '搜索结果'
    : showArchived
      ? activeWorkspace
        ? activeWorkspace.name + ' 的已归档会话'
        : '已归档会话'
      : activeWorkspace
        ? activeWorkspace.name + ' 的会话'
        : '会话';

  // 切换视图/工作区时用新 key 重建滚动容器,让列表回到顶部。
  const listKey = searching
    ? 'search'
    : showArchived
      ? 'archived'
      : 'active:' + (activeWorkspace?.id ?? '');

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="relative px-2.5">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-4 top-1/2 h-3 w-3 -translate-y-1/2 text-ha-faint"
        />
        <input
          aria-label="搜索会话"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索会话"
          className="h-7 w-full rounded-md border border-ha-border bg-ha-panel pl-6 pr-2 text-[12px] text-ha-text-body outline-none placeholder:text-ha-faint focus:border-ha-accent"
        />
      </div>

      <div className="flex items-center justify-between px-2.5">
        <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-semibold text-ha-dim">
          <span className="truncate">
            {showArchived
              ? '已归档'
              : activeWorkspace
                ? `${activeWorkspace.name} 的会话`
                : '会话'}
          </span>
          {!showArchived && activeSessions.length > 0 ? (
            <span className="shrink-0 font-mono text-[10px] font-normal text-ha-faint">
              {activeSessions.length}
            </span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={toggleArchived}
          className="font-mono text-[10px] text-ha-faint transition-colors hover:text-ha-text-2"
        >
          {showArchived ? '返回最近' : '已归档'}
        </button>
      </div>

      {listLoading ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="space-y-2 px-2.5 py-2">
            {[0, 1, 2].map((key) => (
              <div key={key} className="space-y-1.5">
                <div className="h-2.5 w-4/5 animate-pulse rounded bg-ha-panel-2" />
                <div className="h-2 w-1/2 animate-pulse rounded bg-ha-panel-2/70" />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {!listLoading && listFailed ? (
        <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-danger">
          工作区或会话加载失败,稍后再试。
        </p>
      ) : null}

      {!listLoading && !listFailed ? (
        workspaces.length === 0 ? (
          <div className="px-2.5 py-2">
            <p className="text-[12px] leading-5 text-ha-dim">
              还没有工作区,先到工作区页新建一个。
            </p>
            <Link
              to="/workspace?new=1"
              className={cn(
                consoleButtonClass({ variant: 'secondary', size: 'sm' }),
                'mt-2 w-full',
              )}
            >
              <FolderPlus className="h-3.5 w-3.5" />
              创建工作区
            </Link>
          </div>
        ) : entries.length === 0 ? (
          searching ? (
            <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
              无匹配会话
            </p>
          ) : showArchived ? (
            <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
              暂无已归档会话
            </p>
          ) : (
            <div className="px-2.5 py-2">
              <p className="text-[12px] leading-5 text-ha-dim">
                这个工作区还没有会话
              </p>
              <Link
                to={newSessionHref}
                className={cn(
                  consoleButtonClass({
                    variant: 'secondary',
                    size: 'sm',
                  }),
                  'mt-2 w-full',
                )}
              >
                <Plus className="h-3.5 w-3.5" />
                开始新会话
              </Link>
            </div>
          )
        ) : (
          <>
            {searching ? (
              <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-ha-dim">
                搜索结果
              </div>
            ) : null}
            <VirtualList
              key={listKey}
              role="list"
              aria-label={listLabel}
              className="min-h-0 flex-1"
              items={entries}
              itemHeight={sessionItemHeight}
              getKey={sessionItemKey}
              renderItem={(item) =>
                item.kind === 'section' ? (
                  <div className="flex h-8 items-center px-2 pt-1 text-[11px] font-medium text-ha-dim">
                    {item.label}
                  </div>
                ) : (
                  <SessionRow conversation={item.conversation} model={model} />
                )
              }
            />
          </>
        )
      ) : null}

      {conversationsQuery.hasNextPage ? (
        <button
          type="button"
          onClick={() => void conversationsQuery.fetchNextPage()}
          disabled={conversationsQuery.isFetchingNextPage}
          className="mx-2.5 w-[calc(100%-1.25rem)] rounded-md border border-ha-border px-2 py-1.5 text-[11px] text-ha-muted transition-colors hover:border-ha-border-strong hover:text-ha-text-2 disabled:opacity-50"
        >
          {conversationsQuery.isFetchingNextPage ? '加载中…' : '加载更多'}
        </button>
      ) : null}
    </div>
  );
}
