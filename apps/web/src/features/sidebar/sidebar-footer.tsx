/**
 * 侧栏底部:通知入口(整行入口 + 面板向上展开)+ 账户菜单。
 *
 * 账户卡片原来是直链 /settings,和导航里的「设置」重复;C 步骤把它改成菜单:
 * - 触发器:头像 + 账户名 + 账号标签(点击展开);
 * - 菜单向上展开(卡片贴着视口左下角,向下会越过屏幕底边):设置、官网;
 * - 点外部 / Escape 关闭,焦点回到触发器。
 *
 * 通知面板仍由 NotificationCenter 负责,这里只给它一个整行入口与分隔线。
 */
import { ChevronUp } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';

import { NotificationCenter } from '@/features/notifications/notification-center';
import { cn } from '@/lib/utils';

import { SETTINGS_NAV_ITEM, SITE_NAV_ITEM } from './nav-data';

/** 菜单项排版(与工作区切换器菜单保持一致)。 */
const menuItemClass =
  'flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors';

export function SidebarFooter({
  account,
  accountHint,
}: {
  account: string | null;
  accountHint: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // 点外部 / Escape 关闭,焦点回到触发器。
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <>
      {/* 通知入口:整行入口 + 面板向上展开(铃铛贴着左下角,向下展开会被视口裁掉) */}
      <div className="shrink-0 border-b border-ha-border pb-2">
        <NotificationCenter variant="bar" />
      </div>

      <div ref={rootRef} className="relative shrink-0">
        <button
          ref={triggerRef}
          type="button"
          aria-label="账户菜单"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className={cn(
            'flex w-full items-center gap-2.5 rounded-md border bg-ha-panel p-2.5 text-left transition-colors',
            open
              ? 'border-ha-border-strong bg-ha-panel-2'
              : 'border-ha-border hover:border-ha-border-strong',
          )}
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
            className={cn(
              'h-3 w-3 shrink-0 text-ha-dim transition-transform',
              open ? '' : 'rotate-180',
            )}
          />
        </button>

        {open ? (
          <div
            role="menu"
            aria-label="账户"
            className="absolute bottom-full left-0 z-40 mb-1 w-full rounded-lg border border-ha-border-strong bg-ha-panel p-1 shadow-[0_18px_44px_rgba(0,0,0,0.55)]"
          >
            <Link
              to={SETTINGS_NAV_ITEM.to}
              role="menuitem"
              onClick={() => setOpen(false)}
              className={cn(
                menuItemClass,
                'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
              )}
            >
              <span
                aria-hidden="true"
                className="shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5"
              >
                {SETTINGS_NAV_ITEM.icon}
              </span>
              {SETTINGS_NAV_ITEM.label}
            </Link>
            <a
              href={SITE_NAV_ITEM.href}
              role="menuitem"
              target="_blank"
              rel="noreferrer"
              onClick={() => setOpen(false)}
              className={cn(
                menuItemClass,
                'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
              )}
            >
              <span
                aria-hidden="true"
                className="shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5"
              >
                {SITE_NAV_ITEM.icon}
              </span>
              {SITE_NAV_ITEM.label}
            </a>
          </div>
        ) : null}
      </div>
    </>
  );
}
