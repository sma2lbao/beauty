import type { ComponentPropsWithRef, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

export type SelectSize = 'sm' | 'md' | 'lg';

/**
 * Select 选择器。
 *
 * 原生 select 的语义化包装:结构与交互交给浏览器,样式统一 --bd-* 令牌。
 * 自定义箭头用 SVG 绘制,选项通过 children 传入(原生 option/optgroup)。
 */

export interface SelectProps
  extends Omit<ComponentPropsWithRef<'select'>, 'size'> {
  /** 尺寸,与同尺寸的 Input 保持一致高度 */
  size?: SelectSize;
  /** 校验失败态,展示危险色边框 */
  invalid?: boolean;
  /** 占位提示,仅提示用途;空选项请自行在 children 里提供 */
  placeholder?: ReactNode;
}

export function Select({
  size = 'md',
  invalid = false,
  placeholder,
  className,
  disabled,
  children,
  ...rest
}: SelectProps) {
  return (
    <span
      data-slot="select"
      data-size={size}
      data-invalid={invalid || undefined}
      data-disabled={disabled || undefined}
      className={cn(
        // 语义类名,样式见 select.css
        'bd-select',
        `bd-select--${size}`,
        className,
      )}
    >
      {placeholder != null ? (
        <span data-slot="select-placeholder" className="bd-select__placeholder">
          {placeholder}
        </span>
      ) : null}
      <select
        data-slot="select-control"
        className="bd-select__control"
        disabled={disabled}
        aria-invalid={invalid || undefined}
        {...rest}
      >
        {children}
      </select>
      <svg
        data-slot="select-arrow"
        className="bd-select__arrow"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        aria-hidden="true"
      >
        <path
          d="M4 6l4 4 4-4"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
      </svg>
    </span>
  );
}
