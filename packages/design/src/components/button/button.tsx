import type { ComponentPropsWithRef } from 'react';

import { cx } from '../../utils/class-name';

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'danger';

export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ComponentPropsWithRef<'button'> {
  /** 视觉类型,决定按钮在界面中的强调程度 */
  variant?: ButtonVariant;
  /** 尺寸 */
  size?: ButtonSize;
  /** 撑满父容器宽度 */
  block?: boolean;
  /** 加载态:禁用交互并展示加载指示 */
  loading?: boolean;
}

export function Button({
  variant = 'primary',
  size = 'md',
  block = false,
  loading = false,
  type = 'button',
  className,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'bd-button',
        `bd-button--${variant}`,
        `bd-button--${size}`,
        block && 'bd-button--block',
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <span className="bd-button__spinner" aria-hidden="true" />
      ) : null}
      {children}
    </button>
  );
}
