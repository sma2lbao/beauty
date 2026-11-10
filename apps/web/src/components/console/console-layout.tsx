/**
 * 控制台骨架:左侧 264px 导航 + 右侧主区域。
 *
 * 页面自己组合顶栏/页头/状态栏,因为四张设计稿的页头结构不同:
 * 工作台是 52px 顶栏 + 底部状态栏,任务编排/变更与产物是页面大标题 + 操作组。
 * 布局只负责不变的导航与滚动边界,外加全局的命令面板(⌘K)。
 */
import { PanelLeft, Search } from 'lucide-react';
import { Link, Outlet } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { NotificationCenter } from '@/features/notifications/notification-center';
import { ToastViewport } from '@/features/notifications/toast-viewport';
import { ConsoleNavLink } from '@/components/console/ui';
import { ConsoleSidebar } from '@/components/console/sidebar';
import { CommandPalette } from '@/features/command-palette/command-palette';
import { useCommandPaletteShortcut } from '@/features/command-palette/use-command-palette-shortcut';
import { useAccountRole } from '@/features/auth/use-account-role';
import { MOBILE_NAV_ITEMS } from '@/features/sidebar/nav-data';
import { visibleNavItems } from '@/features/sidebar/nav-state';
import { useRunStatusNotifications } from '@/lib/run-status-watcher';
import { useUiStore } from '@/stores/ui-store';

export function ConsoleLayout() {
  const sidebarVisible = useUiStore((state) => state.sidebarVisible);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const setCommandPaletteOpen = useUiStore(
    (state) => state.setCommandPaletteOpen,
  );
  const { isAdmin } = useAccountRole();
  // 移动顶栏与桌面侧栏/命令面板共用同一份过滤:管理员入口只对管理员显示。
  const mobileNavItems = visibleNavItems(MOBILE_NAV_ITEMS, isAdmin);
  // 命令面板挂在布局层:任何页面、侧栏隐藏时都能用 ⌘K 打开。
  useCommandPaletteShortcut();
  // 通知(M0 交互面):全局状态探测(runs 轮询 diff)+ toast 视口挂在布局层,
  // 保证侧栏隐藏/页面切换时通知不中断;通知中心入口在侧栏账户区与移动端顶栏。
  useRunStatusNotifications();
  return (
    <div className="flex h-svh overflow-hidden bg-ha-bg text-ha-text-body">
      {!sidebarVisible ? (
        <button
          type="button"
          aria-label="显示侧边栏"
          title="显示侧边栏"
          onClick={toggleSidebar}
          className="fixed left-3 top-3 z-40 hidden h-7 w-7 place-items-center rounded-md border border-ha-border-strong bg-ha-panel-2 text-ha-muted hover:text-ha-text lg:grid"
        >
          <PanelLeft className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {sidebarVisible ? <ConsoleSidebar /> : null}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-ha-border bg-ha-sidebar px-3 lg:hidden">
          <Link to="/" aria-label="beauty moon 工作台" className="mr-1">
            <BrandLockup size={24} textClassName="text-[14px]" />
          </Link>
          <nav
            aria-label="移动端导航"
            className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
          >
            {mobileNavItems.map((item) => (
              <ConsoleNavLink key={item.to} {...item} compact />
            ))}
          </nav>
          {/* 移动端没有 ⌘K:给命令面板一个可见入口(与桌面侧栏的放大镜一致) */}
          <button
            type="button"
            aria-label="搜索与命令"
            onClick={() => setCommandPaletteOpen(true)}
            className="ml-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
          >
            <Search className="h-4 w-4" />
          </button>
          {/* 通知入口放在顶栏最右:面板向右上展开,贴视口右缘才不会被裁出屏幕 */}
          <div className="ml-1 flex shrink-0 items-center">
            <NotificationCenter />
          </div>
        </div>
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <Outlet />
        </main>
      </div>
      <ToastViewport />
      <CommandPalette />
    </div>
  );
}
