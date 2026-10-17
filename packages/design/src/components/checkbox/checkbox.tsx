import { useId, useState } from 'react';
import type { ComponentPropsWithRef, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * Checkbox 多选框。
 *
 * 原生 input(视觉隐藏)+ label 结构,语义类名见同目录 checkbox.css:
 * 勾选图标用 SVG 描边,选中态由 data-state 表达,键盘可达性与原生一致。
 */

export interface CheckboxProps
  extends Omit<ComponentPropsWithRef<'input'>, 'type'> {
  /** 受控选中状态 */
  checked?: boolean;
  /** 非受控初始状态 */
  defaultChecked?: boolean;
  /** 状态变化时触发,受控与非受控模式下均会调用 */
  onCheckedChange?: (checked: boolean) => void;
  /** 复选框旁的说明文字 */
  label?: ReactNode;
}

export function Checkbox({
  checked,
  defaultChecked = false,
  onCheckedChange,
  label,
  className,
  disabled,
  onChange,
  id,
  children,
  ...rest
}: CheckboxProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const [internalChecked, setInternalChecked] = useState(defaultChecked);
  const isControlled = checked !== undefined;
  const isChecked = isControlled ? checked : internalChecked;

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onChange?.(event);
    if (event.defaultPrevented) return;

    if (!isControlled) {
      setInternalChecked(event.target.checked);
    }
    onCheckedChange?.(event.target.checked);
  };

  return (
    <label
      data-slot="checkbox-root"
      data-disabled={disabled || undefined}
      className="bd-checkbox"
    >
      <input
        id={inputId}
        type="checkbox"
        data-slot="checkbox"
        data-state={isChecked ? 'checked' : 'unchecked'}
        className={cn('bd-checkbox__input', className)}
        checked={isChecked}
        disabled={disabled}
        onChange={handleChange}
        {...rest}
      />
      <span
        data-slot="checkbox-box"
        data-state={isChecked ? 'checked' : 'unchecked'}
        aria-hidden="true"
        className="bd-checkbox__box"
      >
        <svg viewBox="0 0 12 12" width="12" height="12" fill="none">
          <path
            d="M2.5 6.5l2.5 2.5 4.5-5.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      {label != null ? (
        <span data-slot="checkbox-label" className="bd-checkbox__label">
          {label}
        </span>
      ) : null}
      {children}
    </label>
  );
}
