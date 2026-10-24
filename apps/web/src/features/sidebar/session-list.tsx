/**
 * 侧边栏会话区:搜索框 + 视图标题 + 列表(加载/失败/搜索/归档/未分组/空态)+ 加载更多。
 */
import { FolderPlus, Plus, Search } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { cn } from '@/lib/utils';

import { SessionRow } from './session-row';
import type { SidebarModel } from './use-sidebar-model';

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

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-0.5">
        {listLoading ? (
          <div className="space-y-2 px-2.5 py-2">
            {[0, 1, 2].map((key) => (
              <div key={key} className="space-y-1.5">
                <div className="h-2.5 w-4/5 animate-pulse rounded bg-ha-panel-2" />
                <div className="h-2 w-1/2 animate-pulse rounded bg-ha-panel-2/70" />
              </div>
            ))}
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
          ) : searching ? (
            <>
              <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-ha-dim">
                搜索结果
              </div>
              {conversations.length > 0 ? (
                <div role="list" aria-label="搜索结果" className="space-y-0.5">
                  {conversations.map((conversation) => (
                    <SessionRow
                      key={conversation.id}
                      conversation={conversation}
                      model={model}
                    />
                  ))}
                </div>
              ) : (
                <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
                  无匹配会话
                </p>
              )}
            </>
          ) : showArchived ? (
            activeSessions.length > 0 ? (
              <div
                role="list"
                aria-label={
                  activeWorkspace
                    ? `${activeWorkspace.name} 的已归档会话`
                    : '已归档会话'
                }
                className="space-y-0.5"
              >
                {activeSessions.map((conversation) => (
                  <SessionRow
                    key={conversation.id}
                    conversation={conversation}
                    model={model}
                  />
                ))}
              </div>
            ) : (
              <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
                暂无已归档会话
              </p>
            )
          ) : activeSessions.length === 0 && ungroupedSessions.length === 0 ? (
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
          ) : (
            <>
              {activeSessions.length > 0 ? (
                <div
                  role="list"
                  aria-label={
                    activeWorkspace ? `${activeWorkspace.name} 的会话` : '会话'
                  }
                  className="space-y-0.5"
                >
                  {activeSessions.map((conversation) => (
                    <SessionRow
                      key={conversation.id}
                      conversation={conversation}
                      model={model}
                    />
                  ))}
                </div>
              ) : null}
              {ungroupedSessions.length > 0 ? (
                <div className="pt-2">
                  <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-ha-dim">
                    未分组
                  </div>
                  <div
                    role="list"
                    aria-label="未分组会话"
                    className="space-y-0.5"
                  >
                    {ungroupedSessions.map((conversation) => (
                      <SessionRow
                        key={conversation.id}
                        conversation={conversation}
                        model={model}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          )
        ) : null}

        {conversationsQuery.hasNextPage ? (
          <button
            type="button"
            onClick={() => void conversationsQuery.fetchNextPage()}
            disabled={conversationsQuery.isFetchingNextPage}
            className="mx-2.5 mt-1 w-[calc(100%-1.25rem)] rounded-md border border-ha-border px-2 py-1.5 text-[11px] text-ha-muted transition-colors hover:border-ha-border-strong hover:text-ha-text-2 disabled:opacity-50"
          >
            {conversationsQuery.isFetchingNextPage ? '加载中…' : '加载更多'}
          </button>
        ) : null}
      </div>
    </div>
  );
}
