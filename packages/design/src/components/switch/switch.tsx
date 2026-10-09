import { useState } from 'react';
import type { ComponentPropsWithRef, MouseEvent } from 'react';

import { cn } from '../../lib/utils.js';

export interface SwitchProps
  extends Omit<ComponentPropsWithRef<'button'>, 'onChange'> {
  /** 受控开关状态 */
  checked?: boolean;
  /** 非受控初始状态 */
  defaultChecked?: boolean;
  /** 状态变化时触发,受控与非受控模式下均会调用 */
  onCheckedChange?: (checked: boolean) => void;
}

export function Switch({
  checked,
  defaultChecked = false,
  onCheckedChange,
  type = 'button',
  className,
  disabled,
  onClick,
  ...rest
}: SwitchProps) {
  const [internalChecked, setInternalChecked] = useState(defaultChecked);
  const isControlled = checked !== undefined;
  const isChecked = isControlled ? checked : internalChecked;

  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    if (event.defaultPrevented) return;

    if (!isControlled) {
      setInternalChecked((value) => !value);
    }
    onCheckedChange?.(!isChecked);
  };

  return (
    <button
      type={type}
      role="switch"
      data-slot="switch"
      data-state={isChecked ? 'checked' : 'unchecked'}
      aria-checked={isChecked}
      className={cn(
        // 语义类名,样式见 switch.css;选中态由 data-state 表达
        'bd-switch',
        className,
      )}
      disabled={disabled}
      onClick={handleClick}
      {...rest}
    >
      <span
        data-slot="switch-thumb"
        aria-hidden="true"
        className="bd-switch__thumb"
      />
    </button>
  );
}
