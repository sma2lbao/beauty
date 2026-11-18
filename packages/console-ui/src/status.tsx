/**
 * 状态基元:状态点、状态文字、语义标签、状态胶囊、空态。
 *
 * StatusDot / StatusText / ToneBadge / StatusPill 的实现已收敛到
 * @beauty/design 的 Status 组件族(普通 CSS + bd-tone-* 语义类),
 * 这里按旧名称再导出,调用方(web / desktop)的 import 与 props 不变;
 * EmptyPanel 是控制台面板排版,组件库的 Empty 是通用空态,故留在本包。
 */
import type { ReactNode } from 'react';

import { cn } from './cn.js';

export {
  StatusBadge as ToneBadge,
  StatusDot,
  StatusPill,
  StatusText,
} from '@beauty/design';

/** 空态:图标 + 标题 + 说明 + 动作。 */
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
