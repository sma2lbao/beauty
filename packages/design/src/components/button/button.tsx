import type { ComponentPropsWithRef } from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'danger';

export type ButtonSize = 'sm' | 'md' | 'lg';

/**
 * 视觉与尺寸映射为语义类名,样式定义在同目录的 button.css:
 * - 源码里不出现 Tailwind 工具类,结构、样式、主题三者解耦;
 * - 宿主既可以用 `.bd-button--primary` 这类类名覆写,也可以传 `className` 追加自己的类;
 * - 颜色/圆角/字体全部来自 `--bd-*` 令牌,改令牌即可换肤(含暗色)。
 */
const buttonVariants = cva('bd-button', {
  variants: {
    variant: {
      primary: 'bd-button--primary',
      secondary: 'bd-button--secondary',
      outline: 'bd-button--outline',
      ghost: 'bd-button--ghost',
      danger: 'bd-button--danger',
    },
    size: {
      sm: 'bd-button--sm',
      md: 'bd-button--md',
      lg: 'bd-button--lg',
    },
    block: {
      true: 'bd-button--block',
    },
  },
  defaultVariants: {
    variant: 'primary',
    size: 'md',
  },
});

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
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, block }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <span
          data-slot="button-spinner"
          className="bd-button__spinner"
          aria-hidden="true"
        />
      ) : null}
      {children}
    </button>
  );
}

export { buttonVariants };
