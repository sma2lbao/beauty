/**
 * 控制台空态:图标 + 标题 + 说明 + 动作。
 *
 * 排版是控制台面板语言(--ha-* 令牌 + 虚线边框),组件库的 Empty 是通用空态,
 * 所以这份留在 web 自己维护;其它应用需要同样的观感时按各自令牌实现。
 */
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function EmptyPanel({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-ha-border px-6 py-12 text-center',
        className,
      )}
    >
      {icon ? <div className="text-ha-ghost">{icon}</div> : null}
      <p className="text-[13px] font-medium text-ha-text-2">{title}</p>
      {description ? (
        <p className="max-w-sm text-[12px] leading-5 text-ha-dim">
          {description}
        </p>
      ) : null}
      {action}
    </div>
  );
}
