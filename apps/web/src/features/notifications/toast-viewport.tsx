/**
 * 右下角 toast 视口:渲染 notifications store 里的 toast,到期自动消失。
 * 固定挂在 ConsoleLayout,所有页面共享;不阻塞页面滚动,可手动关闭。
 */
import { BellOff, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';
import { useEffect } from 'react';

import {
  useNotificationsStore,
  type NotificationTone,
} from '@/stores/notifications-store';
import { cn } from '@/lib/utils';

const TONE_ICON: Record<NotificationTone, typeof Info> = {
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  danger: BellOff,
};

function ToastCard({
  id,
  tone,
  title,
  description,
  expiresAt,
}: {
  id: string;
  tone: NotificationTone;
  title: string;
  description?: string;
  expiresAt: number;
}) {
  const dismiss = useNotificationsStore((state) => state.dismissToast);
  useEffect(() => {
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      dismiss(id);
      return;
    }
    const timer = setTimeout(() => dismiss(id), remaining);
    return () => clearTimeout(timer);
  }, [id, expiresAt, dismiss]);

  const Icon = TONE_ICON[tone] ?? Info;
  return (
    <div
      role={tone === 'danger' || tone === 'warning' ? 'alert' : 'status'}
      className={cn(
        'pointer-events-auto flex w-80 items-start gap-2.5 rounded-lg border bg-ha-panel px-3.5 py-3 shadow-xl backdrop-blur',
        tone === 'success' && 'border-ha-success/40',
        tone === 'warning' && 'border-ha-warning/40',
        tone === 'danger' && 'border-ha-danger/40',
        tone === 'info' && 'border-ha-border-strong',
      )}
    >
      <Icon
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          tone === 'success' && 'text-ha-success',
          tone === 'warning' && 'text-ha-warning',
          tone === 'danger' && 'text-ha-danger',
          tone === 'info' && 'text-ha-info',
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium leading-5 text-ha-text">
          {title}
        </p>
        {description ? (
          <p className="mt-0.5 line-clamp-2 text-[12px] leading-4 text-ha-muted">
            {description}
          </p>
        ) : null}
      </div>
      <button
        type="button"
        aria-label="关闭通知"
        onClick={() => dismiss(id)}
        className="rounded p-0.5 text-ha-faint transition-colors hover:text-ha-text"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function ToastViewport() {
  const toasts = useNotificationsStore((state) => state.toasts);
  return (
    <div
      aria-label="通知浮层"
      className="pointer-events-none fixed bottom-10 right-4 z-50 flex flex-col-reverse gap-2"
    >
      {toasts.map((toast) => (
        <ToastCard key={toast.id} {...toast} />
      ))}
    </div>
  );
}
