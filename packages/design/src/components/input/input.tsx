import type { ComponentPropsWithRef, ReactNode } from 'react';

import { cx } from '../../utils/class-name';

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
      className={cx(
        'bd-input',
        `bd-input--${size}`,
        disabled && 'is-disabled',
        invalid && 'is-invalid',
        className,
      )}
    >
      {prefix != null ? (
        <span className="bd-input__prefix">{prefix}</span>
      ) : null}
      <input
        className="bd-input__control"
        disabled={disabled}
        aria-invalid={invalid || undefined}
        {...rest}
      />
      {suffix != null ? (
        <span className="bd-input__suffix">{suffix}</span>
      ) : null}
    </span>
  );
}
