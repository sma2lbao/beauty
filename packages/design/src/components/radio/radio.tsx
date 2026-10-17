import {
  createContext,
  useContext,
  useId,
  useState,
  type ComponentPropsWithRef,
  type ReactNode,
} from 'react';

import { cn } from '../../lib/utils.js';

interface RadioGroupContextValue {
  name: string;
  value?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
}

const RadioGroupContext = createContext<RadioGroupContextValue | null>(null);

export type RadioGroupOrientation = 'vertical' | 'horizontal';

export interface RadioGroupProps
  extends Omit<
    ComponentPropsWithRef<'div'>,
    'onChange' | 'defaultValue' | 'value'
  > {
  /** 受控选中值 */
  value?: string;
  /** 非受控初始值 */
  defaultValue?: string;
  /** 选中项变化时触发 */
  onValueChange?: (value: string) => void;
  /** 原生 name;缺省自动生成,组内单选互斥与方向键导航依赖它 */
  name?: string;
  /** 整组禁用 */
  disabled?: boolean;
  /** 排列方向 */
  orientation?: RadioGroupOrientation;
}

/**
 * RadioGroup 单选组:配合 Radio 使用。
 *
 * 组内共享原生 name,因此浏览器自带的"方向键切换 + 组内只选中一个"行为直接生效;
 * 语义类名见同目录 radio.css。
 */
export function RadioGroup({
  value,
  defaultValue,
  onValueChange,
  name,
  disabled = false,
  orientation = 'vertical',
  className,
  children,
  ...rest
}: RadioGroupProps) {
  const autoName = useId();
  const isControlled = value !== undefined;
  const [internalValue, setInternalValue] = useState(defaultValue);
  const activeValue = isControlled ? value : internalValue;

  const handleChange = (next: string) => {
    if (!isControlled) setInternalValue(next);
    onValueChange?.(next);
  };

  return (
    <div
      role="radiogroup"
      aria-orientation={orientation}
      data-slot="radio-group"
      data-disabled={disabled || undefined}
      className={cn(
        'bd-radio-group',
        `bd-radio-group--${orientation}`,
        className,
      )}
      {...rest}
    >
      <RadioGroupContext.Provider
        value={{
          name: name ?? `bd-radio-${autoName.replace(/:/g, '')}`,
          value: activeValue,
          onValueChange: handleChange,
          disabled,
        }}
      >
        {children}
      </RadioGroupContext.Provider>
    </div>
  );
}

export interface RadioProps
  extends Omit<ComponentPropsWithRef<'input'>, 'type' | 'onChange' | 'value'> {
  /** 该项的值 */
  value: string;
  /** 选项文字 */
  label?: ReactNode;
}

/**
 * Radio 单选项:原生 input(视觉隐藏)+ label 结构。
 * 放在 RadioGroup 内使用;单独使用时不带选中态(由浏览器默认行为决定)。
 */
export function Radio({
  value,
  label,
  className,
  disabled,
  id,
  ...rest
}: RadioProps) {
  const group = useContext(RadioGroupContext);
  const autoId = useId();
  const inputId = id ?? autoId;
  const checked = group ? group.value === value : undefined;
  const isDisabled = disabled || group?.disabled;

  return (
    <label
      data-slot="radio-root"
      data-disabled={isDisabled || undefined}
      className="bd-radio"
    >
      <input
        id={inputId}
        type="radio"
        data-slot="radio"
        data-state={checked ? 'checked' : 'unchecked'}
        className={cn('bd-radio__input', className)}
        name={group?.name}
        value={value}
        checked={checked}
        disabled={isDisabled}
        onChange={() => group?.onValueChange?.(value)}
        {...rest}
      />
      <span
        data-slot="radio-box"
        data-state={checked ? 'checked' : 'unchecked'}
        aria-hidden="true"
        className="bd-radio__box"
      >
        <span className="bd-radio__dot" />
      </span>
      {label != null ? (
        <span data-slot="radio-label" className="bd-radio__label">
          {label}
        </span>
      ) : null}
    </label>
  );
}
