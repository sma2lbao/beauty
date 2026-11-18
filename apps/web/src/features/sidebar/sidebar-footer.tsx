/**
 * 侧栏底部:通知入口(整行入口 + 面板向上展开)+ 账户菜单。
 *
 * 账户卡片原来是直链 /settings,和导航里的「设置」重复;C 步骤把它改成菜单:
 * - 触发器:头像 + 账户名 + 账号标签(点击展开);
 * - 菜单向上展开(卡片贴着视口左下角,向下会越过屏幕底边):设置、官网;
 * - 点外部 / Escape 关闭,焦点回到触发器(由 DropdownMenu 统一处理)。
 *
 * 通知面板仍由 NotificationCenter 负责,这里只给它一个整行入口与分隔线。
 */
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@beauty/design';
import { ChevronUp } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { NotificationCenter } from '@/features/notifications/notification-center';

import { SETTINGS_NAV_ITEM, SITE_NAV_ITEM } from './nav-data';

export function SidebarFooter({
  account,
  accountHint,
}: {
  account: string | null;
  accountHint: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* 通知入口:整行入口 + 面板向上展开(铃铛贴着左下角,向下展开会被视口裁掉) */}
      <div className="shrink-0 border-b border-ha-border pb-2">
        <NotificationCenter variant="bar" />
      </div>

      <DropdownMenu open={open} onOpenChange={setOpen}>
        <div className="relative shrink-0">
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="账户菜单"
              className="group flex w-full items-center gap-2.5 rounded-md border border-ha-border bg-ha-panel p-2.5 text-left transition-colors hover:border-ha-border-strong data-[state=open]:border-ha-border-strong data-[state=open]:bg-ha-panel-2"
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
                  {accountHint}
                </span>
              </span>
              <ChevronUp
                aria-hidden="true"
                className="h-3 w-3 shrink-0 rotate-180 text-ha-dim transition-transform group-data-[state=open]:rotate-0"
              />
            </button>
          </DropdownMenuTrigger>

          <DropdownMenuContent aria-label="账户" side="top" className="w-full">
            <DropdownMenuItem
              asChild
              className="gap-2.5 text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2"
            >
              <Link to={SETTINGS_NAV_ITEM.to}>
                <span
                  aria-hidden="true"
                  className="shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5"
                >
                  {SETTINGS_NAV_ITEM.icon}
                </span>
                {SETTINGS_NAV_ITEM.label}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem
              asChild
              className="gap-2.5 text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2"
            >
              <a href={SITE_NAV_ITEM.href} target="_blank" rel="noreferrer">
                <span
                  aria-hidden="true"
                  className="shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5"
                >
                  {SITE_NAV_ITEM.icon}
                </span>
                {SITE_NAV_ITEM.label}
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </div>
      </DropdownMenu>
    </>
  );
}
