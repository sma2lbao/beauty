import {
  cloneElement,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';
import type {
  ComponentPropsWithRef,
  MouseEvent as ReactMouseEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MutableRefObject,
  ReactElement,
  ReactNode,
  Ref,
} from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

/* ── 尺寸与状态 ───────────────────────────────────────────────────────── */

/** 菜单密度:sm 用于密集的操作列表,md 用于导航/账户菜单。 */
export type DropdownMenuSize = 'sm' | 'md';

/** 面板对齐:start/end 贴触发器左/右,stretch 与触发器同宽。 */
export type DropdownMenuAlign = 'start' | 'end' | 'stretch';

/** 面板展开方向。 */
export type DropdownMenuSide = 'bottom' | 'top';

type FocusIntent = 'container' | 'first' | 'last';

const DropdownMenuSizeContext = createContext<DropdownMenuSize>('md');

/* ── 根上下文 ─────────────────────────────────────────────────────────── */

interface DropdownMenuContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerId: string;
  contentId: string;
  triggerRef: MutableRefObject<HTMLElement | null>;
  contentRef: MutableRefObject<HTMLDivElement | null>;
  /** 记录下次展开时焦点放到哪:点开是面板本身,方向键打开是首/末项。 */
  setFocusIntent: (intent: FocusIntent) => void;
}

const DropdownMenuContext = createContext<DropdownMenuContextValue | null>(
  null,
);

function useDropdownMenuContext(component: string) {
  const context = useContext(DropdownMenuContext);
  if (!context) {
    throw new Error(`${component} 必须渲染在 <DropdownMenu> 内`);
  }
  return context;
}

const MENU_ITEM_SELECTOR = '[role^="menuitem"]:not([disabled])';

function getMenuItems(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(
    container.querySelectorAll<HTMLElement>(MENU_ITEM_SELECTOR),
  );
}

/* ── asChild 的 Slot 合并 ─────────────────────────────────────────────── */

function composeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (node: T | null) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === 'function') {
        ref(node);
      } else {
        (ref as MutableRefObject<T | null>).current = node;
      }
    }
  };
}

/**
 * 把菜单行为合并到唯一子元素上(路由 Link、自定义按钮等)。
 * 事件处理器链式调用:先跑菜单逻辑,子元素自身的处理器仍会执行。
 */
function mergeSlot(
  child: ReactElement<Record<string, unknown>>,
  slotProps: Record<string, unknown>,
): ReactElement<Record<string, unknown>> {
  const childProps = (child.props ?? {}) as Record<string, unknown>;
  const merged: Record<string, unknown> = { ...slotProps };

  for (const key of Object.keys(childProps)) {
    if (key === 'ref') continue;
    const parentValue = slotProps[key];
    const childValue = childProps[key];

    if (typeof parentValue === 'function' && typeof childValue === 'function') {
      merged[key] = (...args: unknown[]) => {
        (parentValue as (...a: unknown[]) => void)(...args);
        (childValue as (...a: unknown[]) => void)(...args);
      };
    } else {
      merged[key] = childValue ?? parentValue;
    }
  }

  const ref = composeRefs(
    slotProps.ref as Ref<unknown> | undefined,
    childProps.ref as Ref<unknown> | undefined,
  );
  if (slotProps.ref || childProps.ref) merged.ref = ref;

  merged.className = cn(
    slotProps.className as string | undefined,
    childProps.className as string | undefined,
  );

  return cloneElement(child, merged);
}

/* ── DropdownMenu(根) ─────────────────────────────────────────────────── */

export interface DropdownMenuProps {
  /** 受控展开态 */
  open?: boolean;
  /** 非受控初始展开态 */
  defaultOpen?: boolean;
  /** 展开态变化回调(点外部、Escape、选中项都会走这里) */
  onOpenChange?: (open: boolean) => void;
  children?: ReactNode;
}

/**
 * DropdownMenu:下拉菜单根组件,提供展开态与触发器/面板的关联。
 *
 * 根组件不渲染额外 DOM——面板用绝对定位,使用方需要给触发器与面板的共同父级
 * 加 position: relative(没有 portal,层级跟随文档流,不引第三方定位依赖)。
 */
export function DropdownMenu({
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  children,
}: DropdownMenuProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  const triggerRef = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const focusIntentRef = useRef<FocusIntent>('container');
  const baseId = useId().replace(/:/g, '');
  const triggerId = `bd-dropdown-menu-trigger-${baseId}`;
  const contentId = `bd-dropdown-menu-content-${baseId}`;

  const setOpen = useCallback(
    (next: boolean) => {
      if (next === open) return;
      if (controlledOpen === undefined) setUncontrolledOpen(next);
      onOpenChange?.(next);
      if (!next) focusIntentRef.current = 'container';
    },
    [controlledOpen, onOpenChange, open],
  );

  // 点外部收起(不抢焦点);Escape 收起并把焦点还给触发器。
  useEffect(() => {
    if (!open) return undefined;

    const handlePointerDown = (event: globalThis.MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (triggerRef.current?.contains(target)) return;
      if (contentRef.current?.contains(target)) return;
      setOpen(false);
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, setOpen]);

  // 展开后按打开方式落焦点:点开落面板,方向键打开落首/末项。
  useEffect(() => {
    if (!open) return;
    const content = contentRef.current;
    if (!content) return;
    const intent = focusIntentRef.current;
    focusIntentRef.current = 'container';
    const items = getMenuItems(content);
    if (intent === 'first' && items[0]) {
      items[0].focus();
    } else if (intent === 'last' && items.length > 0) {
      items[items.length - 1].focus();
    } else {
      content.focus({ preventScroll: true });
    }
  }, [open]);

  // 关闭后焦点若落到 body(选中项被卸载),还给触发器;用户主动点别处时不抢焦点。
  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !open) {
      const active = document.activeElement;
      if (!active || active === document.body) triggerRef.current?.focus();
    }
    wasOpen.current = open;
  }, [open]);

  const setFocusIntent = useCallback((intent: FocusIntent) => {
    focusIntentRef.current = intent;
  }, []);

  const value: DropdownMenuContextValue = {
    open,
    setOpen,
    triggerId,
    contentId,
    triggerRef,
    contentRef,
    setFocusIntent,
  };

  return (
    <DropdownMenuContext.Provider value={value}>
      {children}
    </DropdownMenuContext.Provider>
  );
}

/* ── 触发器 ───────────────────────────────────────────────────────────── */

export interface DropdownMenuTriggerProps extends ComponentPropsWithRef<'button'> {
  /**
   * 把交互挂到唯一子元素上(如 <Button> / 路由 Link),渲染为子元素本身。
   * 子元素会获得 aria-expanded / aria-controls / onClick / ref 的合并结果。
   */
  asChild?: boolean;
}

/** 触发器:默认渲染 button;传 asChild 时复用子元素的外观。 */
export function DropdownMenuTrigger({
  asChild = false,
  type = 'button',
  className,
  children,
  onClick,
  onKeyDown,
  ...rest
}: DropdownMenuTriggerProps) {
  const context = useDropdownMenuContext('DropdownMenuTrigger');
  const { open, setOpen, triggerRef } = context;

  const triggerRefCallback = useCallback(
    (node: HTMLButtonElement | null) => {
      triggerRef.current = node;
    },
    [triggerRef],
  );

  const handleClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    if (event.defaultPrevented) return;
    setOpen(!open);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      context.setFocusIntent(event.key === 'ArrowDown' ? 'first' : 'last');
      setOpen(true);
    }
  };

  const sharedProps = {
    ...rest,
    id: context.triggerId,
    type,
    'aria-haspopup': 'menu' as const,
    'aria-expanded': open,
    'aria-controls': open ? context.contentId : undefined,
    'data-state': open ? 'open' : 'closed',
    onClick: handleClick,
    onKeyDown: handleKeyDown,
  };

  if (asChild) {
    if (!isValidElement(children)) return null;
    return mergeSlot(children as ReactElement<Record<string, unknown>>, {
      ...sharedProps,
      ref: triggerRefCallback,
    });
  }

  return (
    <button
      {...sharedProps}
      ref={triggerRefCallback}
      data-slot="dropdown-menu-trigger"
      className={cn('bd-dropdown-menu__trigger', className)}
    >
      {children}
    </button>
  );
}

/* ── 面板 ─────────────────────────────────────────────────────────────── */

const CONTENT_ALIGN_CLASS: Record<DropdownMenuAlign, string> = {
  start: 'bd-dropdown-menu__content--start',
  end: 'bd-dropdown-menu__content--end',
  stretch: 'bd-dropdown-menu__content--stretch',
};

const CONTENT_SIDE_CLASS: Record<DropdownMenuSide, string> = {
  bottom: 'bd-dropdown-menu__content--bottom',
  top: 'bd-dropdown-menu__content--top',
};

export interface DropdownMenuContentProps extends Omit<
  ComponentPropsWithRef<'div'>,
  'role'
> {
  align?: DropdownMenuAlign;
  side?: DropdownMenuSide;
  /** 菜单密度,作用于所有菜单项;sm 12px 密排,md 13px 整行 32px */
  size?: DropdownMenuSize;
  /**
   * 容器角色,默认 menu(命令项)。展开式导航里放的是普通链接时传 null,
   * 保留链接语义——把页面链接塞进 role=menu 会让读屏念成命令项。
   */
  role?: ComponentPropsWithRef<'div'>['role'] | null;
}

/**
 * 下拉面板:绝对定位 + 浮层视觉。关闭时不渲染,使用方不需要自己写条件渲染。
 */
export function DropdownMenuContent({
  align = 'start',
  side = 'bottom',
  size = 'md',
  role: roleProp = 'menu',
  className,
  children,
  id,
  onKeyDown,
  ...rest
}: DropdownMenuContentProps) {
  const context = useDropdownMenuContext('DropdownMenuContent');
  const { open, setOpen, contentRef } = context;

  const contentRefCallback = useCallback(
    (node: HTMLDivElement | null) => {
      contentRef.current = node;
    },
    [contentRef],
  );

  if (!open) return null;

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;

    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      context.triggerRef.current?.focus();
      return;
    }
    // Tab 离开面板即收起(非模态菜单不做焦点陷阱)
    if (event.key === 'Tab') {
      setOpen(false);
      return;
    }

    const items = getMenuItems(contentRef.current);
    if (items.length === 0) return;

    const current = document.activeElement as HTMLElement | null;
    const index = current ? items.indexOf(current) : -1;
    let nextIndex: number;

    if (event.key === 'ArrowDown') {
      nextIndex = index < 0 ? 0 : (index + 1) % items.length;
    } else if (event.key === 'ArrowUp') {
      nextIndex = index <= 0 ? items.length - 1 : index - 1;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = items.length - 1;
    } else {
      return;
    }

    event.preventDefault();
    items[nextIndex]?.focus();
  };

  return (
    <DropdownMenuSizeContext.Provider value={size}>
      <div
        {...rest}
        id={id ?? context.contentId}
        ref={contentRefCallback}
        role={roleProp ?? undefined}
        tabIndex={-1}
        data-slot="dropdown-menu-content"
        data-state="open"
        className={cn(
          'bd-dropdown-menu__content',
          CONTENT_ALIGN_CLASS[align],
          CONTENT_SIDE_CLASS[side],
          className,
        )}
        onKeyDown={handleKeyDown}
      >
        {children}
      </div>
    </DropdownMenuSizeContext.Provider>
  );
}

/* ── 菜单项 ───────────────────────────────────────────────────────────── */

const dropdownMenuItemVariants = cva('bd-dropdown-menu__item', {
  variants: {
    size: {
      sm: 'bd-dropdown-menu__item--sm',
      md: 'bd-dropdown-menu__item--md',
    },
  },
  defaultVariants: {
    size: 'md',
  },
});

export interface DropdownMenuItemProps extends Omit<
  ComponentPropsWithRef<'button'>,
  'onSelect'
> {
  /** 把菜单项行为挂到唯一子元素上(路由 Link / a),保持链接语义 */
  asChild?: boolean;
  /** 选中时回调;随后菜单自动收起 */
  onSelect?: (event: ReactMouseEvent<HTMLElement>) => void;
}

/** 菜单项:button 语义(role=menuitem),点击后菜单自动收起。 */
export function DropdownMenuItem({
  asChild = false,
  onSelect,
  className,
  children,
  onClick,
  disabled,
  ...rest
}: DropdownMenuItemProps) {
  const context = useDropdownMenuContext('DropdownMenuItem');
  const size = useContext(DropdownMenuSizeContext);

  const handleClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    if (event.defaultPrevented) return;
    onSelect?.(event as unknown as ReactMouseEvent<HTMLElement>);
    if (event.defaultPrevented) return;
    context.setOpen(false);
  };

  const itemProps = {
    ...rest,
    role: 'menuitem',
    tabIndex: -1,
    disabled: asChild ? undefined : disabled,
    'aria-disabled': asChild && disabled ? true : undefined,
    'data-disabled': disabled ? 'true' : undefined,
    onClick: handleClick,
  };

  if (asChild) {
    if (!isValidElement(children)) return null;
    return mergeSlot(children as ReactElement<Record<string, unknown>>, {
      ...itemProps,
      className: cn(dropdownMenuItemVariants({ size }), className),
    });
  }

  return (
    <button
      {...itemProps}
      type="button"
      data-slot="dropdown-menu-item"
      className={cn(dropdownMenuItemVariants({ size }), className)}
    >
      {children}
    </button>
  );
}

/* ── 单选菜单项 ───────────────────────────────────────────────────────── */

export interface DropdownMenuRadioItemProps extends Omit<
  ComponentPropsWithRef<'button'>,
  'onSelect'
> {
  /** 是否选中 */
  checked?: boolean;
  /** 选中时回调;随后菜单自动收起 */
  onSelect?: (event: ReactMouseEvent<HTMLElement>) => void;
}

/** 单选菜单项(role=menuitemradio):工作区/视图切换这类互斥选择。 */
export function DropdownMenuRadioItem({
  checked = false,
  onSelect,
  className,
  children,
  onClick,
  ...rest
}: DropdownMenuRadioItemProps) {
  const context = useDropdownMenuContext('DropdownMenuRadioItem');
  const size = useContext(DropdownMenuSizeContext);

  return (
    <button
      {...rest}
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      tabIndex={-1}
      data-slot="dropdown-menu-radio-item"
      data-state={checked ? 'checked' : 'unchecked'}
      className={cn(dropdownMenuItemVariants({ size }), className)}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        onSelect?.(event);
        if (event.defaultPrevented) return;
        context.setOpen(false);
      }}
    >
      {children}
    </button>
  );
}

/* ── 单选分组 / 分组 / 标题 / 分隔线 ──────────────────────────────────── */

export type DropdownMenuRadioGroupProps = ComponentPropsWithRef<'div'>;

/** 单选菜单项的分组容器(role=group)。 */
export function DropdownMenuRadioGroup({
  className,
  children,
  ...rest
}: DropdownMenuRadioGroupProps) {
  return (
    <div
      {...rest}
      role="group"
      data-slot="dropdown-menu-radio-group"
      className={cn('bd-dropdown-menu__group', className)}
    >
      {children}
    </div>
  );
}

export type DropdownMenuGroupProps = ComponentPropsWithRef<'div'>;

/** 菜单分组,给菜单项一个语义分组。 */
export function DropdownMenuGroup({
  className,
  children,
  ...rest
}: DropdownMenuGroupProps) {
  return (
    <div
      {...rest}
      role="group"
      data-slot="dropdown-menu-group"
      className={cn('bd-dropdown-menu__group', className)}
    >
      {children}
    </div>
  );
}

export type DropdownMenuLabelProps = ComponentPropsWithRef<'div'>;

/** 菜单分组标题(仅视觉,不进入焦点顺序)。 */
export function DropdownMenuLabel({
  className,
  children,
  ...rest
}: DropdownMenuLabelProps) {
  return (
    <div
      {...rest}
      data-slot="dropdown-menu-label"
      className={cn('bd-dropdown-menu__label', className)}
    >
      {children}
    </div>
  );
}

export type DropdownMenuSeparatorProps = ComponentPropsWithRef<'div'>;

/** 菜单分隔线(装饰)。 */
export function DropdownMenuSeparator({
  className,
  ...rest
}: DropdownMenuSeparatorProps) {
  return (
    <div
      {...rest}
      role="separator"
      aria-orientation="horizontal"
      data-slot="dropdown-menu-separator"
      className={cn('bd-dropdown-menu__separator', className)}
    />
  );
}

export { dropdownMenuItemVariants };
