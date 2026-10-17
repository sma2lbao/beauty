import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

export type AlertVariant =
  | 'info'
  | 'success'
  | 'warning'
  | 'danger'
  | 'neutral';

const alertVariants = cva('bd-alert', {
  variants: {
    variant: {
      info: 'bd-alert--info',
      success: 'bd-alert--success',
      warning: 'bd-alert--warning',
      danger: 'bd-alert--danger',
      neutral: 'bd-alert--neutral',
    },
  },
  defaultVariants: {
    variant: 'info',
  },
});

/** 各语义色对应的内联图标路径(24 画布,描边由 CSS 控制)。 */
const ICON_PATHS: Record<AlertVariant, string> = {
  info: 'M12 8v.01M12 11v5',
  success: 'M8.5 12.5l2.5 2.5 4.5-5',
  warning: 'M12 8v4.5M12 16v.01',
  danger: 'M9 9l6 6M15 9l-6 6',
  neutral: 'M12 8v.01M12 11v5',
};

export interface AlertProps
  extends Omit<ComponentPropsWithoutRef<'div'>, 'title'> {
  /** 语义类型 */
  variant?: AlertVariant;
  /** 标题,可选;正文放在 children */
  title?: ReactNode;
}

/**
 * Alert 提示条:页面级的说明、警告与错误。
 *
 * - 语义类名见同目录 alert.css,颜色全部来自 --bd-* 令牌;
 * - danger / warning 默认 role="alert"(动态出现时会被读屏立即播报),
 *   其余默认 role="status";可显式传 role 覆盖。
 */
export function Alert({
  variant = 'info',
  title,
  className,
  children,
  role,
  ...rest
}: AlertProps) {
  const resolvedRole =
    role ??
    (variant === 'danger' || variant === 'warning' ? 'alert' : 'status');

  return (
    <div
      data-slot="alert"
      data-variant={variant}
      role={resolvedRole}
      className={cn(alertVariants({ variant }), className)}
      {...rest}
    >
      <svg
        data-slot="alert-icon"
        className="bd-alert__icon"
        viewBox="0 0 24 24"
        width="16"
        height="16"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" />
        <path
          d={ICON_PATHS[variant]}
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
      <div className="bd-alert__content">
        {title != null ? (
          <p data-slot="alert-title" className="bd-alert__title">
            {title}
          </p>
        ) : null}
        {children != null ? (
          <p data-slot="alert-description" className="bd-alert__description">
            {children}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export { alertVariants };
