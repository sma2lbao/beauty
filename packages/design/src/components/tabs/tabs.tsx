import { useId, useRef, useState } from 'react';
import type { ComponentPropsWithoutRef, KeyboardEvent, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

export type TabsSize = 'sm' | 'md';

export interface TabItem {
  /** 唯一标识,对应受控 value */
  value: string;
  /** 页签文字 */
  label: ReactNode;
  /** 面板内容;不传则只渲染页签条(用于切换外部内容) */
  content?: ReactNode;
  /** 是否禁用 */
  disabled?: boolean;
}

export interface TabsProps
  extends Omit<ComponentPropsWithoutRef<'div'>, 'onChange' | 'defaultValue'> {
  /** 页签数据 */
  items: TabItem[];
  /** 受控选中值 */
  value?: string;
  /** 非受控初始值,缺省取第一项 */
  defaultValue?: string;
  /** 选中项变化时触发 */
  onValueChange?: (value: string) => void;
  /** 尺寸 */
  size?: TabsSize;
  /** 撑满父容器宽度(页签等分),用于登录/注册这类分段切换 */
  block?: boolean;
}

/**
 * Tabs 页签:在同层内容间切换。
 *
 * 数据驱动(items 数组),页签条用 role="tablist"/"tab",面板用 role="tabpanel";
 * 键盘支持 ←/→ 与 Home/End,按 WAI-ARIA 的 tabs 模式实现。
 * 不传 content 时只渲染页签条,由使用方自己渲染切换后的内容。
 */
export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  size = 'md',
  block = false,
  className,
  ...rest
}: TabsProps) {
  const baseId = useId().replace(/:/g, '');
  const isControlled = value !== undefined;
  const [internalValue, setInternalValue] = useState(
    () => defaultValue ?? items[0]?.value ?? '',
  );
  const activeValue = isControlled ? value : internalValue;
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const select = (next: string) => {
    if (!isControlled) setInternalValue(next);
    onValueChange?.(next);
  };

  const enabled = items.filter((item) => !item.disabled);
  const hasPanel = items.some((item) => item.content != null);
  const activeItem = items.find((item) => item.value === activeValue);

  const focusTab = (nextValue: string) => {
    select(nextValue);
    tabRefs.current.get(nextValue)?.focus();
  };

  const handleKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    current: string,
  ) => {
    if (enabled.length === 0) return;
    const index = enabled.findIndex((item) => item.value === current);

    if (event.key === 'ArrowRight') {
      event.preventDefault();
      focusTab(enabled[(index + 1) % enabled.length].value);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      focusTab(enabled[(index - 1 + enabled.length) % enabled.length].value);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTab(enabled[0].value);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTab(enabled[enabled.length - 1].value);
    }
  };

  return (
    <div
      data-slot="tabs"
      data-size={size}
      className={cn(
        'bd-tabs',
        `bd-tabs--${size}`,
        block && 'bd-tabs--block',
        className,
      )}
      {...rest}
    >
      <div
        data-slot="tabs-list"
        role="tablist"
        aria-orientation="horizontal"
        className="bd-tabs__list"
      >
        {items.map((item) => {
          const selected = item.value === activeValue;
          return (
            <button
              key={item.value}
              ref={(element) => {
                if (element) tabRefs.current.set(item.value, element);
                else tabRefs.current.delete(item.value);
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${item.value}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.value}`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              data-slot="tabs-tab"
              data-state={selected ? 'active' : 'inactive'}
              className="bd-tabs__tab"
              onClick={() => select(item.value)}
              onKeyDown={(event) => handleKeyDown(event, item.value)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {hasPanel ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${activeValue}`}
          aria-labelledby={`${baseId}-tab-${activeValue}`}
          data-slot="tabs-panel"
          data-state="active"
          tabIndex={0}
          className="bd-tabs__panel"
        >
          {activeItem?.content}
        </div>
      ) : null}
    </div>
  );
}
