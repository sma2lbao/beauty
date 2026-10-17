import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * Empty 空态:列表/页面无内容时的占位说明。
 *
 * 语义类名见同目录 empty.css;图形用内联 SVG(月亮剪影,呼应品牌),
 * 描述与操作区由使用方按需传入。
 */

export interface EmptyProps extends ComponentPropsWithoutRef<'div'> {
  /** 主说明,通常一句话描述当前为空的内容 */
  description?: ReactNode;
  /** 操作区,通常放 Button / Link */
  actions?: ReactNode;
}

export function Empty({
  description,
  actions,
  className,
  children,
  ...rest
}: EmptyProps) {
  return (
    <div data-slot="empty" className={cn('bd-empty', className)} {...rest}>
      <span
        data-slot="empty-figure"
        className="bd-empty__figure"
        aria-hidden="true"
      >
        <svg viewBox="0 0 64 64" width="64" height="64" fill="none">
          <circle cx="32" cy="32" r="22" fill="currentColor" opacity="0.12" />
          <path
            d="M40 14a22 22 0 1 0 10 30 18 18 0 0 1-10-30z"
            fill="currentColor"
            opacity="0.35"
          />
        </svg>
      </span>
      {description != null ? (
        <p data-slot="empty-description" className="bd-empty__description">
          {description}
        </p>
      ) : null}
      {actions != null ? (
        <div data-slot="empty-actions" className="bd-empty__actions">
          {actions}
        </div>
      ) : null}
      {children}
    </div>
  );
}
