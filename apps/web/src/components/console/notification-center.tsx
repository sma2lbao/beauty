/**
 * 通知中心:铃铛 + 未读角标 + 下拉面板(最近通知/全部已读)。
 * 点击通知项跳转 to 地址;Escape/外点关闭;焦点回到铃铛(基础 a11y)。
 *
 * 两种挂载形态(面板展开方向由形态决定,避免被视口/侧栏边界裁切):
 * - `icon`(默认):顶栏/移动端顶栏的小铃铛,面板在下方右对齐;
 * - `bar`:侧栏底部整行入口,面板向上展开且宽度贴合侧栏——铃铛贴着视口左下角,
 *   往下右展开会同时越过屏幕左边与底边;
 */
import { Bell, CheckCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';

import {
  unreadCount,
  useNotificationsStore,
  type AppNotification,
} from '@/stores/notifications-store';
import { relativeTime, runStatusTone } from '@/lib/presentation';
import { cn } from '@/lib/utils';

/** 通知时间(相对)——presentation.relativeTime 吃 ISO 字符串,这里转一下。 */
function notificationTime(issuedAt: number): string {
  const iso = new Date(issuedAt).toISOString();
  return relativeTime(iso);
}

function NotificationRow({
  notification,
  unread,
  onOpen,
}: {
  notification: AppNotification;
  unread: boolean;
  onOpen: (n: AppNotification) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(notification)}
      className={cn(
        'flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-ha-panel-2/70',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
          unread
            ? notification.tone === 'success'
              ? 'bg-ha-success'
              : notification.tone === 'warning'
                ? 'bg-ha-warning'
                : notification.tone === 'danger'
                  ? 'bg-ha-danger'
                  : 'bg-ha-info'
            : 'bg-transparent',
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] leading-5 text-ha-text">
          {notification.title}
        </span>
        {notification.description ? (
          <span className="block truncate text-[11px] leading-4 text-ha-faint">
            {notification.description}
          </span>
        ) : null}
        <span className="block text-[10px] leading-4 text-ha-dim">
          {notificationTime(notification.issuedAt)}
        </span>
      </span>
    </button>
  );
}

export type NotificationCenterVariant = 'icon' | 'bar';

/** 未读角标:图标形态浮在铃铛右上角,整行形态排在行尾。 */
const BADGE_CLASS =
  'grid h-3.5 min-w-3.5 place-items-center rounded-full bg-ha-danger px-0.5 font-mono text-[9px] font-semibold leading-none text-white';

export function NotificationCenter({
  variant = 'icon',
}: {
  variant?: NotificationCenterVariant;
} = {}) {
  const bar = variant === 'bar';
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const bellRef = useRef<HTMLButtonElement | null>(null);
  const navigate = useNavigate();
  const notifications = useNotificationsStore((state) => state.notifications);
  const readCursor = useNotificationsStore((state) => state.readCursor);
  const markAllRead = useNotificationsStore((state) => state.markAllRead);
  const unread = unreadCount({ notifications, readCursor });

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        bellRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const openNotification = (notification: AppNotification) => {
    setOpen(false);
    markAllRead();
    if (notification.to) {
      navigate(notification.to);
    }
  };

  return (
    <div ref={containerRef} className={cn('relative', bar && 'w-full')}>
      <button
        ref={bellRef}
        type="button"
        aria-label={unread > 0 ? `通知(${unread} 条未读)` : '通知'}
        aria-expanded={open}
        title="通知"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          bar
            ? 'flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-ha-text-3 transition-colors hover:bg-ha-panel-2/70 hover:text-ha-text-2'
            : 'relative grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text',
        )}
      >
        <Bell className="h-4 w-4 shrink-0" />
        {bar ? <span className="truncate text-[13px]">通知</span> : null}
        {unread > 0 ? (
          <span
            aria-hidden
            className={cn(
              BADGE_CLASS,
              bar ? 'ml-auto' : 'absolute -right-0.5 -top-0.5',
            )}
          >
            {unread > 9 ? '9+' : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="通知面板"
          className={cn(
            'absolute z-50 rounded-lg border border-ha-border-strong bg-ha-panel shadow-2xl',
            // 侧栏底部入口:向上展开、宽度贴合父容器(侧栏),最多占六成屏高
            bar
              ? 'bottom-full left-0 right-0 mb-2 flex max-h-[min(24rem,60svh)] flex-col overflow-hidden'
              : 'right-0 top-9 w-[min(20rem,calc(100vw-1.5rem))]',
          )}
        >
          <div className="flex shrink-0 items-center justify-between border-b border-ha-border px-3 py-2">
            <span className="text-[12px] font-medium text-ha-text">通知</span>
            <button
              type="button"
              onClick={markAllRead}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] text-ha-faint transition-colors hover:text-ha-text"
            >
              <CheckCheck className="h-3 w-3" />
              全部已读
            </button>
          </div>
          <div
            className={cn(
              'p-1.5',
              bar
                ? 'min-h-0 flex-1 overflow-y-auto'
                : 'max-h-80 overflow-y-auto',
            )}
          >
            {notifications.length === 0 ? (
              <p className="px-2.5 py-6 text-center text-[12px] text-ha-dim">
                还没有通知。
              </p>
            ) : (
              notifications.map((notification) => (
                <NotificationRow
                  key={notification.id}
                  notification={notification}
                  unread={notification.issuedAt > readCursor}
                  onOpen={openNotification}
                />
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// runStatusTone 仅作类型对齐保留引用(通知 tone 与 run 状态色一致),防止将来改色不同步。
void runStatusTone;
