/**
 * 控制台侧边栏:品牌 + 新建任务 + 主导航 + 最近会话 + 账户。
 *
 * 会话管理(M0-09):
 * - 标题搜索(q)与归档视图(archived)走服务端分页;
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
  BookOpen,
  Columns3,
  FolderKanban,
  GitCompareArrows,
  Globe,
  Inbox,
  LayoutGrid,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Settings,
  Trash2,
} from 'lucide-react';
import { useDeferredValue, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
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
import { cn } from '@/lib/utils';

const PRIMARY_NAV = [
  { to: '/', end: true, icon: <LayoutGrid />, label: '工作台' },
  { to: '/runs', icon: <Columns3 />, label: '任务编排' },
  { to: '/changes', icon: <GitCompareArrows />, label: '变更' },
  { to: '/knowledge', icon: <BookOpen />, label: '知识库' },
  { to: '/settings', icon: <Settings />, label: '设置' },
];

const SECONDARY_NAV = [
  { to: '/workspace', icon: <FolderKanban />, label: '工作区' },
  { to: '/outbox', icon: <Inbox />, label: '事件队列' },
  { to: '/landing', icon: <Globe />, label: '官网' },
];

export function ConsoleSidebar() {
  const [searchParams] = useSearchParams();
  const selectedConversation = searchParams.get('conversation');
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [showArchived, setShowArchived] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{
    id: string;
    title: string;
  } | null>(null);

  const conversationsQuery = useInfiniteQuery({
    queryKey: ['conversations', 'page', deferredQuery, showArchived],
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

  const invalidateConversations = () => {
    void queryClient.invalidateQueries({ queryKey: ['conversations'] });
    void queryClient.invalidateQueries({ queryKey: ['conversation'] });
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
        navigate('/?new=1', { replace: true });
      }
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => conversationsApi.remove(id),
    onSuccess: (_data, id) => {
      invalidateConversations();
      if (id === selectedConversation) {
        navigate('/?new=1', { replace: true });
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

  return (
    <aside
      aria-label="控制台导航"
      className="hidden h-full w-[264px] shrink-0 flex-col gap-[18px] border-r border-ha-border bg-ha-sidebar px-3 py-4 lg:flex"
    >
      <div className="flex items-center justify-between">
        <Link to="/" aria-label="beauty moon 工作台">
          <BrandLockup size={28} />
        </Link>
        <span className="inline-flex h-5 items-center rounded-[4px] border border-ha-border-strong bg-ha-panel-2 px-1.5 font-mono text-[10px] font-medium text-ha-muted">
          PROD
        </span>
      </div>

      <Link
        to="/?new=1"
        className={cn(
          consoleButtonClass({ variant: 'primary', size: 'lg' }),
          'w-full',
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        新建任务
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
            placeholder="搜索会话…"
            className="h-7 w-full rounded-md border border-ha-border bg-ha-panel pl-6 pr-2 text-[12px] text-ha-text-body outline-none placeholder:text-ha-faint focus:border-ha-accent"
          />
        </div>

        <div className="flex items-center justify-between px-2.5">
          <span className="text-[11px] font-semibold text-ha-dim">
            {showArchived ? '已归档' : '最近会话'}
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
          {conversationsQuery.isPending ? (
            <div className="space-y-2 px-2.5 py-2">
              {[0, 1, 2].map((key) => (
                <div key={key} className="space-y-1.5">
                  <div className="h-2.5 w-4/5 animate-pulse rounded bg-ha-panel-2" />
                  <div className="h-2 w-1/2 animate-pulse rounded bg-ha-panel-2/70" />
                </div>
              ))}
            </div>
          ) : null}

          {!conversationsQuery.isPending && conversations.length === 0 ? (
            <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
              {showArchived
                ? '没有已归档的会话。'
                : query.trim()
                  ? '没有匹配的会话。'
                  : '还没有会话。点上方「新建任务」开始。'}
            </p>
          ) : null}

          {conversations.map((conversation) => {
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
                    to={'/?conversation=' + conversation.id}
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
                      重命名
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
                      {showArchived ? '取消归档' : '归档'}
                    </button>
                    <button
                      type="button"
                      onClick={() => confirmDelete(conversation)}
                      className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-danger hover:bg-ha-danger-bg"
                    >
                      <Trash2 className="h-3 w-3" />
                      删除
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}

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

      <nav aria-label="更多" className="flex flex-col gap-0.5">
        {SECONDARY_NAV.map((item) => (
          <ConsoleNavLink key={item.to} {...item} />
        ))}
      </nav>

      <Link
        to="/settings"
        className="flex items-center gap-2.5 rounded-md border border-ha-border bg-ha-panel p-2.5 transition-colors hover:border-ha-border-strong"
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
