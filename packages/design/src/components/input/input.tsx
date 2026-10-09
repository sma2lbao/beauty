import type { ComponentPropsWithRef, ReactNode } from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

export type InputSize = 'sm' | 'md' | 'lg';

export interface InputProps
  extends Omit<ComponentPropsWithRef<'input'>, 'size' | 'prefix'> {
  /** 尺寸,与同尺寸的 Button 保持一致高度 */
  size?: InputSize;
  /** 校验失败态,展示危险色边框 */
  invalid?: boolean;
  /** 前置内容,常用于图标或单位 */
  prefix?: ReactNode;
  /** 后置内容,常用于图标或单位 */
  suffix?: ReactNode;
}

const inputGroupVariants = cva(
  // 语义类名,样式见 input.css;invalid / disabled 由 data-* 属性表达
  'bd-input-group',
  {
    variants: {
      size: {
        sm: 'bd-input-group--sm',
        md: 'bd-input-group--md',
        lg: 'bd-input-group--lg',
      },
    },
    defaultVariants: {
      size: 'md',
    },
  },
);

export function Input({
  size = 'md',
  invalid = false,
  prefix,
  suffix,
  className,
  disabled,
  ...rest
}: InputProps) {
  return (
    <span
      data-slot="input-group"
      data-invalid={invalid || undefined}
      data-disabled={disabled || undefined}
      className={cn(inputGroupVariants({ size }), className)}
    >
      {prefix != null ? (
        <span data-slot="input-prefix" className="bd-input__prefix">
          {prefix}
        </span>
      ) : null}
      <input
        data-slot="input"
        className="bd-input"
        disabled={disabled}
        aria-invalid={invalid || undefined}
        {...rest}
      />
      {suffix != null ? (
        <span data-slot="input-suffix" className="bd-input__suffix">
          {suffix}
        </span>
      ) : null}
    </span>
  );
}
