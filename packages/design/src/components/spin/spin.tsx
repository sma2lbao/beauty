import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';

export type SpinSize = 'sm' | 'md' | 'lg';

/**
 * Spin 加载指示:旋转圆环。
 *
 * 语义类名见同目录 spin.css;尺寸三档,文本标签可选,
 * 有标签时自动挂 aria-live 让屏幕阅读器感知。
 */

export interface SpinProps extends ComponentPropsWithoutRef<'span'> {
  /** 尺寸 */
  size?: SpinSize;
  /** 加载说明文字,提供后与指示器横向排列 */
  label?: string;
}

export function Spin({
  size = 'md',
  label,
  className,
  role = 'status',
  children,
  ...rest
}: SpinProps) {
  return (
    <span
      data-slot="spin"
      className={cn('bd-spin', `bd-spin--${size}`, className)}
      role={role}
      {...rest}
    >
      <svg
        data-slot="spin-indicator"
        className="bd-spin__indicator"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle
          className="bd-spin__track"
          cx="12"
          cy="12"
          r="9"
          strokeWidth="2.5"
        />
        <circle
          className="bd-spin__arc"
          cx="12"
          cy="12"
          r="9"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray="42"
          strokeDashoffset="28"
        />
      </svg>
      {label != null ? (
        <span data-slot="spin-label" className="bd-spin__label">
          {label}
        </span>
      ) : null}
      {children}
    </span>
  );
}
