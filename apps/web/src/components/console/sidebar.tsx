/**
 * 控制台侧边栏:品牌 + 新建任务 + 主导航 + 最近会话 + 账户。
 *
 * 对应设计稿《01 主控台 · 会话工作台》左侧 264px 栏。会话状态点不额外发明字段:
 * 拿 runs 列表按 conversation_id 归并出最新一条 run,用它的状态上色;
 * 没有 run 的会话显示中性点。点会话走 `/?conversation=<id>`,由工作台页接管。
 */
import { useQuery } from '@tanstack/react-query';
import {
  BookOpen,
  Columns3,
  FolderKanban,
  GitCompareArrows,
  Globe,
  Inbox,
  LayoutGrid,
  Plus,
  Settings,
} from 'lucide-react';
import { Link, useSearchParams } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
import { ConsoleNavLink, StatusDot } from '@/components/console/ui';
import { accountApi } from '@/lib/account-api';
import { accountLabel } from '@/lib/auth';
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

  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: conversationsApi.list,
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

  const conversations = conversationsQuery.data ?? [];
  const latestRunByConversation = new Map<string, Run>();
  for (const run of runsQuery.data ?? []) {
    if (!run.conversation_id) continue;
    const previous = latestRunByConversation.get(run.conversation_id);
    if (!previous || Date.parse(run.created_at) > Date.parse(previous.created_at)) {
      latestRunByConversation.set(run.conversation_id, run);
    }
  }

  const account = accountQuery.data ?? null;
  const plan = organizationQuery.data?.plan;
  const planLabel = plan ? `${plan} 版` : '个人工作区';

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

      <div className="flex min-h-0 flex-1 flex-col gap-2.5">
        <div className="flex items-center justify-between px-2.5">
          <span className="text-[11px] font-semibold text-ha-dim">最近会话</span>
          <span className="font-mono text-[11px] font-medium text-ha-faint">
            {conversations.length}
          </span>
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
              还没有会话。点上方「新建任务」开始。
            </p>
          ) : null}

          {conversations.slice(0, 12).map((conversation) => {
            const run = latestRunByConversation.get(conversation.id);
            const tone = run ? runStatusTone(run.status) : 'neutral';
            const meta = run
              ? `${RUN_STATUS_LABELS[run.status]} · ${relativeTime(conversation.updated_at)}`
              : relativeTime(conversation.updated_at);
            const selected = conversation.id === selectedConversation;
            return (
              <Link
                key={conversation.id}
                to={`/?conversation=${conversation.id}`}
                aria-current={selected ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-[9px] rounded-md px-2.5 py-[9px] transition-colors',
                  selected ? 'bg-[#1c1d1f]' : 'hover:bg-ha-panel-2/60',
                )}
              >
                <StatusDot tone={tone} pulse={run?.status === 'running'} />
                <span className="min-w-0 flex-1">
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
                </span>
              </Link>
            );
          })}
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
