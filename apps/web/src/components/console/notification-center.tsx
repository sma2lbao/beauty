/**
 * 顶栏通知中心:铃铛 + 未读角标 + 下拉面板(最近通知/全部已读)。
 * 点击通知项跳转 to 地址;Escape/外点关闭;焦点回到铃铛(基础 a11y)。
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

export function NotificationCenter() {
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
    <div ref={containerRef} className="relative">
      <button
        ref={bellRef}
        type="button"
        aria-label={unread > 0 ? `通知(${unread} 条未读)` : '通知'}
        aria-expanded={open}
        title="通知"
        onClick={() => setOpen((value) => !value)}
        className="relative grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 ? (
          <span
            aria-hidden
            className={cn(
              'absolute -right-0.5 -top-0.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-ha-danger px-0.5',
              'font-mono text-[9px] font-semibold leading-none text-white',
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
          className="absolute right-0 top-9 z-50 w-80 rounded-lg border border-ha-border-strong bg-ha-panel shadow-2xl"
        >
          <div className="flex items-center justify-between border-b border-ha-border px-3 py-2">
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
          <div className="max-h-80 overflow-y-auto p-1.5">
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
