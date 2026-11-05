/**
 * 「更多」入口:把资源 / 管理两组收进一个弹层,侧栏只留三个高频道具。
 *
 * 为什么是「展开式导航(disclosure)」而不是 role=menu:
 * 弹层里是普通页面链接,保留 link 语义对读屏更友好(菜单语义会把
 * 6 个二级页伪装成命令项);键盘操作只需 Enter 展开 + Tab 依次走。
 *
 * 细节:
 * - 开合状态按路由记(`openAt === pathname`):选完一项跳转后自动是收起态,
 *   不需要 effect 同步,也不会出现「弹层跟着用户飘到新页面」;
 * - 当前路由落在弹层里时,触发器左侧亮一个点,并保持高亮底色;
 * - 点外部 / Escape 收起(Escape 焦点回触发器);触发器是 button 而非链接。
 */
import { ChevronDown, Ellipsis } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router';

import { ConsoleNavLink } from '@/components/console/ui';
import { cn } from '@/lib/utils';

import { MORE_GROUPS, isNavItemActive } from './nav-state';

const PANEL_ID = 'sidebar-more-menu';

export function MoreNav() {
  const { pathname } = useLocation();
  const [openAt, setOpenAt] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const open = openAt === pathname;
  const active = MORE_GROUPS.some((group) =>
    group.items.some((item) => isNavItemActive(item, pathname)),
  );

  const close = () => setOpenAt(null);

  // 点到别处 / Escape 收起;焦点只在 Escape 时回触发器(鼠标点别处不该抢焦点)。
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpenAt(null);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenAt(null);
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
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={PANEL_ID}
        data-active={active ? 'true' : undefined}
        onClick={() => setOpenAt(open ? null : pathname)}
        className={cn(
          'flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors',
          open || active
            ? 'bg-[#1c1d1f] text-ha-text'
            : 'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
        )}
      >
        <span
          aria-hidden="true"
          className="relative shrink-0 [&>svg]:h-4 [&>svg]:w-4"
        >
          <Ellipsis />
          {active && !open ? (
            <span className="absolute -left-1.5 top-1/2 h-1 w-1 -translate-y-1/2 rounded-full bg-ha-accent-soft" />
          ) : null}
        </span>
        <span className="flex-1 truncate text-left">更多</span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            'h-3 w-3 shrink-0 text-ha-ghost transition-transform',
            open ? 'rotate-180' : '',
          )}
        />
      </button>

      {open ? (
        <div
          id={PANEL_ID}
          className="absolute left-0 right-0 top-full z-40 mt-1 rounded-lg border border-ha-border-strong bg-ha-panel p-1 shadow-[0_18px_44px_rgba(0,0,0,0.55)]"
        >
          {MORE_GROUPS.map((group, index) => (
            <div key={group.id} className="flex flex-col gap-0.5">
              {index > 0 ? (
                <div aria-hidden="true" className="my-1 h-px bg-ha-border" />
              ) : null}
              <p className="px-2.5 pb-0.5 pt-1 text-[11px] font-semibold text-ha-dim">
                {group.label}
              </p>
              {group.items.map((item) => (
                <ConsoleNavLink key={item.to} {...item} onClick={close} />
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
