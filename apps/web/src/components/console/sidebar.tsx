/**
 * 控制台侧边栏:品牌 + 新会话 + 主导航 + 按工作区分组的会话 + 账户。
 *
 * 交互对齐 dsh 侧栏的「工作区 / 会话」心智:
 * - URL ?workspace=<id> 是当前工作区的唯一真源,切换工作区只改 URL;
 * - 会话区直接按工作区分组列出会话(分组逻辑在 lib/session-groups),
 *   组头点击切换当前工作区,组头右侧「+」在该工作区新建会话;
 * - 列表取租户全部会话(不按工作区过滤),客户端分组;搜索/归档仍走服务端分页;
 * - 重命名 / 归档 / 软删除就地操作,成功后失效会话查询并同步当前选中会话;
 * - 分页用 keyset 游标,加载更多不丢不重。
 */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Archive,
  BarChart3,
  BookOpen,
  Columns3,
  FolderKanban,
  FolderPlus,
  GitCompareArrows,
  Globe,
  Inbox,
  LayoutGrid,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Plug,
  Plus,
  Search,
  Settings,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { useDeferredValue, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
import { NotificationCenter } from '@/components/console/notification-center';
import { ConsoleNavLink, StatusDot } from '@/components/console/ui';
import { accountApi } from '@/lib/account-api';
import { accountLabel } from '@/lib/auth';
import type { Conversation } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import {
  RUN_STATUS_LABELS,
  relativeTime,
  runStatusTone,
} from '@/lib/presentation';
import type { Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import type { SessionGroup } from '@/lib/session-groups';
import { groupSessionsByWorkspace } from '@/lib/session-groups';
import { siteUrl } from '@/lib/site-links';
import { cn } from '@/lib/utils';
import { useWorkspaceSelection } from '@/lib/workspace-selection';
import { useUiStore } from '@/stores/ui-store';

const PRIMARY_NAV = [
  { to: '/', end: true, icon: <LayoutGrid />, label: '工作台' },
  { to: '/runs', icon: <Columns3 />, label: '任务编排' },
  { to: '/changes', icon: <GitCompareArrows />, label: '变更' },
  { to: '/mcp', icon: <Plug />, label: 'MCP' },
  { to: '/skills', icon: <Sparkles />, label: 'Skills' },
  { to: '/usage', icon: <BarChart3 />, label: '用量' },
  { to: '/knowledge', icon: <BookOpen />, label: '知识库' },
  { to: '/settings', icon: <Settings />, label: '设置' },
];

const SECONDARY_NAV = [
  { to: '/workspace', icon: <FolderKanban />, label: '工作区' },
  { to: '/outbox', icon: <Inbox />, label: '事件队列' },
];

export function ConsoleSidebar() {
  const [searchParams] = useSearchParams();
  const selectedConversation = searchParams.get('conversation');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setSidebarVisible = useUiStore((state) => state.setSidebarVisible);
  const {
    workspaces: workspaceList,
    activeWorkspaceId,
    isLoading: workspacesLoading,
    error: workspacesError,
    selectWorkspace,
  } = useWorkspaceSelection();
  const workspaces = workspaceList.filter(Boolean);
  const workspaceIds = new Set(workspaces.map((workspace) => workspace.id));

  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [showArchived, setShowArchived] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{
    id: string;
    title: string;
  } | null>(null);

  // 取租户内全部会话(不传 workspace_id),客户端再按工作区分组;
  // 搜索与归档视图仍交给服务端分页。
  const conversationsQuery = useInfiniteQuery({
    queryKey: ['conversations', 'all', showArchived, deferredQuery],
    queryFn: ({ pageParam }) =>
      conversationsApi.page({
        limit: 20,
        q: deferredQuery,
        archived: showArchived,
        cursor: pageParam as string | undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: runsApi.list,
    refetchInterval: 5000,
  });
  const accountQuery = useQuery({
    queryKey: ['auth-account'],
    queryFn: accountLabel,
  });
  const organizationQuery = useQuery({
    queryKey: ['organization'],
    queryFn: accountApi.organization,
    retry: false,
  });

  const conversations =
    conversationsQuery.data?.pages.flatMap((page) => page.items ?? []) ?? [];
  const searching = deferredQuery.trim().length > 0;
  const sessionGroups = groupSessionsByWorkspace(conversations, workspaces);

  const invalidateConversations = () => {
    void queryClient.invalidateQueries({ queryKey: ['conversations'] });
    void queryClient.invalidateQueries({ queryKey: ['conversation'] });
  };

  /** 控制台内所有会话链接都带上工作区:刷新/分享都能还原同一视图。 */
  const workspaceHref = (
    workspaceId: string | null,
    options: { conversation?: string; new?: boolean } = {},
  ) => {
    const search = new URLSearchParams();
    if (workspaceId) search.set('workspace', workspaceId);
    if (options.conversation) search.set('conversation', options.conversation);
    if (options.new) search.set('new', '1');
    const serialized = search.toString();
    return serialized ? '/?' + serialized : '/';
  };

  const newSessionHref = activeWorkspaceId
    ? workspaceHref(activeWorkspaceId, { new: true })
    : '/workspace?new=1';

  /**
   * 会话链接归属的工作区:会话自身的归属优先;
   * 归属为空或已不在工作区清单里(工作区被删)时回落到当前工作区,避免死链。
   */
  const sessionWorkspaceId = (conversation: Conversation): string | null => {
    const workspaceId = conversation.workspace_id;
    if (workspaceId && workspaceIds.has(workspaceId)) {
      return workspaceId;
    }
    return activeWorkspaceId;
  };

  const renameMutation = useMutation({
    mutationFn: (input: { id: string; title: string }) =>
      conversationsApi.update(input.id, { title: input.title }),
    onSuccess: () => {
      setRenaming(null);
      invalidateConversations();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (input: { id: string; archived: boolean }) =>
      conversationsApi.update(input.id, { archived: input.archived }),
    onSuccess: (_data, input) => {
      invalidateConversations();
      if (input.archived && input.id === selectedConversation) {
        navigate(workspaceHref(activeWorkspaceId, { new: true }), {
          replace: true,
        });
      }
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => conversationsApi.remove(id),
    onSuccess: (_data, id) => {
      invalidateConversations();
      if (id === selectedConversation) {
        navigate(workspaceHref(activeWorkspaceId, { new: true }), {
          replace: true,
        });
      }
    },
  });

  const latestRunByConversation = new Map<string, Run>();
  for (const run of runsQuery.data ?? []) {
    if (!run.conversation_id) continue;
    const previous = latestRunByConversation.get(run.conversation_id);
    if (
      !previous ||
      Date.parse(run.created_at) > Date.parse(previous.created_at)
    ) {
      latestRunByConversation.set(run.conversation_id, run);
    }
  }

  const account = accountQuery.data ?? null;
  const plan = organizationQuery.data?.plan;
  const planLabel = plan ? plan + ' 版' : '个人工作区';

  const confirmDelete = (conversation: Conversation) => {
    setMenuFor(null);
    const ok =
      typeof window.confirm !== 'function' ||
      window.confirm('删除会话「' + conversation.title + '」?该操作不可撤销。');
    if (ok) removeMutation.mutate(conversation.id);
  };

  /** 单条会话:状态点 + 标题/元信息 + 就地操作菜单(重命名/归档/删除)。 */
  const sessionRow = (conversation: Conversation) => {
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
  };

  /** 一个工作区分组:可点击的组头(含「+」新建会话)+ 组内会话行。 */
  const renderSessionGroup = (group: SessionGroup) => {
    const workspaceId = group.workspaceId;
    const active = workspaceId !== null && workspaceId === activeWorkspaceId;
    const count = group.sessions.length;

    return (
      <div
        key={group.key}
        role="group"
        aria-label={group.name}
        className="space-y-0.5"
      >
        {workspaceId ? (
          <div
            className={cn(
              'group/workspace flex items-center gap-0.5 rounded-md pr-1 transition-colors',
              active ? 'bg-ha-accent-soft/20' : 'hover:bg-ha-panel-2/40',
            )}
          >
            <button
              type="button"
              aria-current={active ? 'true' : undefined}
              onClick={() =>
                selectWorkspace(workspaceId, { keepConversation: true })
              }
              className="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 text-left outline-none focus-visible:bg-ha-panel-2/60"
            >
              <FolderKanban
                aria-hidden="true"
                className={cn(
                  'h-3 w-3 shrink-0',
                  active ? 'text-ha-accent-soft' : 'text-ha-faint',
                )}
              />
              <span
                className={cn(
                  'truncate text-[11px] font-medium',
                  active ? 'text-ha-text' : 'text-ha-dim',
                )}
              >
                {group.name}
              </span>
              {count > 0 ? (
                <span className="shrink-0 font-mono text-[10px] text-ha-faint">
                  {count} 个会话
                </span>
              ) : null}
            </button>
            <Link
              to={workspaceHref(workspaceId, { new: true })}
              aria-label={'在「' + group.name + '」中新建会话'}
              title={'在「' + group.name + '」中新建会话'}
              className="grid h-5 w-5 shrink-0 place-items-center rounded text-ha-faint opacity-0 transition-opacity hover:bg-ha-panel-2 hover:text-ha-text-2 focus-visible:opacity-100 group-hover/workspace:opacity-100"
            >
              <Plus className="h-3 w-3" />
            </Link>
          </div>
        ) : (
          <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-ha-dim">
            {group.name}
          </div>
        )}
        {group.sessions.map(sessionRow)}
      </div>
    );
  };

  const listLoading = workspacesLoading || conversationsQuery.isPending;
  const listFailed = Boolean(workspacesError) || conversationsQuery.isError;

  return (
    <aside
      aria-label="控制台导航"
      className="hidden h-full w-[264px] shrink-0 flex-col gap-[18px] border-r border-ha-border bg-ha-sidebar px-3 py-4 lg:flex"
    >
      <div className="flex items-center justify-between">
        <Link to="/" aria-label="beauty moon 工作台">
          <BrandLockup size={28} />
        </Link>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex h-5 items-center rounded-[4px] border border-ha-border-strong bg-ha-panel-2 px-1.5 font-mono text-[10px] font-medium text-ha-muted">
            PROD
          </span>
          <button
            type="button"
            aria-label="隐藏侧边栏"
            title="隐藏侧边栏"
            onClick={() => setSidebarVisible(false)}
            className="grid h-6 w-6 place-items-center rounded text-ha-faint hover:bg-ha-panel-2 hover:text-ha-text-2"
          >
            <PanelLeftClose className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <Link
        to={newSessionHref}
        className={cn(
          consoleButtonClass({ variant: 'primary', size: 'lg' }),
          'w-full',
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        新会话
      </Link>

      <nav aria-label="主导航" className="flex flex-col gap-0.5">
        {PRIMARY_NAV.map((item) => (
          <ConsoleNavLink key={item.to} {...item} />
        ))}
      </nav>

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
          <span className="text-[11px] font-semibold text-ha-dim">
            {showArchived ? '已归档' : '会话'}
          </span>
          <button
            type="button"
            onClick={() => {
              setShowArchived((value) => !value);
              setMenuFor(null);
            }}
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
                  conversations.map(sessionRow)
                ) : (
                  <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
                    无匹配会话
                  </p>
                )}
              </>
            ) : conversations.length === 0 ? (
              <div className="px-2.5 py-2">
                {showArchived ? (
                  <p className="text-[12px] leading-5 text-ha-dim">
                    暂无已归档会话
                  </p>
                ) : (
                  <>
                    <p className="text-[12px] leading-5 text-ha-dim">
                      暂无会话
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
                  </>
                )}
              </div>
            ) : (
              sessionGroups.map(renderSessionGroup)
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

        <Link
          to="/workspace?new=1"
          className="flex h-7 shrink-0 items-center gap-2 rounded-md px-2.5 text-[12px] text-ha-dim transition-colors hover:bg-ha-panel-2/60 hover:text-ha-text-2"
        >
          <FolderPlus aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          添加工作区
        </Link>
      </div>

      <nav aria-label="更多" className="flex flex-col gap-0.5">
        {SECONDARY_NAV.map((item) => (
          <ConsoleNavLink key={item.to} {...item} />
        ))}
        <a
          href={siteUrl('/')}
          target="_blank"
          rel="noreferrer"
          className="flex h-8 items-center gap-2.5 rounded-md px-2.5 text-ha-text-3 transition-colors hover:bg-ha-panel-2/70 hover:text-ha-text-2"
        >
          <Globe aria-hidden="true" className="h-4 w-4 shrink-0" />
          <span className="text-[13px]">官网</span>
        </a>
      </nav>

      {/* 通知入口:整行入口 + 面板向上展开(铃铛贴着左下角,向下展开会被视口裁掉) */}
      <div className="shrink-0 border-b border-ha-border pb-2">
        <NotificationCenter variant="bar" />
      </div>

      <Link
        to="/settings"
        className="flex shrink-0 items-center gap-2.5 rounded-md border border-ha-border bg-ha-panel p-2.5 transition-colors hover:border-ha-border-strong"
      >
        <span
          aria-hidden="true"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#2a2d35] font-mono text-[11px] text-ha-muted"
        >
          {(account?.trim().charAt(0) || '访').toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-ha-text">
            {account ?? '未登录'}
          </span>
          <span className="mt-0.5 block truncate font-mono text-[11px] text-ha-dim">
            {planLabel}
          </span>
        </span>
      </Link>
    </aside>
  );
}
