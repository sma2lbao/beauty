/**
 * 控制台骨架:左侧 264px 导航 + 右侧主区域。
 *
 * 页面自己组合顶栏/页头/状态栏,因为四张设计稿的页头结构不同:
 * 工作台是 52px 顶栏 + 底部状态栏,任务编排/变更与产物是页面大标题 + 操作组。
 * 布局只负责不变的导航与滚动边界。
 */
import { BookOpen, Columns3, GitCompareArrows, LayoutGrid, Settings } from 'lucide-react';
import { Link, Outlet, useLocation } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { ConsoleNavLink } from '@/components/console/ui';
import { ConsoleSidebar } from '@/components/console/sidebar';

const MOBILE_NAV = [
  { to: '/', end: true, icon: <LayoutGrid />, label: '工作台' },
  { to: '/runs', icon: <Columns3 />, label: '任务编排' },
  { to: '/changes', icon: <GitCompareArrows />, label: '变更' },
  { to: '/knowledge', icon: <BookOpen />, label: '知识库' },
  { to: '/settings', icon: <Settings />, label: '设置' },
];

export function ConsoleLayout() {
  const location = useLocation();
  const onLanding = location.pathname.startsWith('/landing');

  return (
    <div className="flex h-svh overflow-hidden bg-ha-bg text-ha-text-body">
      <ConsoleSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        {onLanding ? null : (
          <div className="flex h-12 shrink-0 items-center gap-2 border-b border-ha-border bg-ha-sidebar px-3 lg:hidden">
            <Link to="/" aria-label="beauty moon 工作台" className="mr-1">
              <BrandLockup size={24} textClassName="text-[14px]" />
            </Link>
            <nav
              aria-label="移动端导航"
              className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
            >
              {MOBILE_NAV.map((item) => (
                <ConsoleNavLink key={item.to} {...item} compact />
              ))}
            </nav>
          </div>
        )}
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
