import { useState } from 'react';
import type { ComponentPropsWithRef, MouseEvent } from 'react';

import { cx } from '../../utils/class-name';

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
      aria-checked={isChecked}
      className={cx(
        'bd-switch',
        isChecked && 'is-checked',
        disabled && 'is-disabled',
        className,
      )}
      disabled={disabled}
      onClick={handleClick}
      {...rest}
    >
      <span className="bd-switch__thumb" aria-hidden="true" />
    </button>
  );
}
