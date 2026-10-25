/**
 * 侧边栏主导航与更多导航(含官网外链)。
 */
import {
  BarChart3,
  BookOpen,
  Brain,
  Columns3,
  FolderKanban,
  GitCompareArrows,
  Globe,
  Inbox,
  LayoutGrid,
  Plug,
  Settings,
  Sparkles,
} from 'lucide-react';

import { ConsoleNavLink } from '@/components/console/ui';
import { siteUrl } from '@/lib/site-links';

const PRIMARY_NAV = [
  { to: '/', end: true, icon: <LayoutGrid />, label: '工作台' },
  { to: '/runs', icon: <Columns3 />, label: '任务编排' },
  { to: '/changes', icon: <GitCompareArrows />, label: '变更' },
  { to: '/mcp', icon: <Plug />, label: 'MCP' },
  { to: '/skills', icon: <Sparkles />, label: 'Skills' },
  { to: '/usage', icon: <BarChart3 />, label: '用量' },
  { to: '/knowledge', icon: <BookOpen />, label: '知识库' },
  { to: '/memory', icon: <Brain />, label: '记忆' },
  { to: '/settings', icon: <Settings />, label: '设置' },
];

const SECONDARY_NAV = [
  { to: '/workspace', icon: <FolderKanban />, label: '工作区' },
  { to: '/outbox', icon: <Inbox />, label: '事件队列' },
];

export function PrimaryNav() {
  return (
    <nav aria-label="主导航" className="flex flex-col gap-0.5">
      {PRIMARY_NAV.map((item) => (
        <ConsoleNavLink key={item.to} {...item} />
      ))}
    </nav>
  );
}

export function MoreNav() {
  return (
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
  );
}
