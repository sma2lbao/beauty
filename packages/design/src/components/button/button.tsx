import {
  cloneElement,
  isValidElement,
  type ComponentPropsWithRef,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  type ReactNode,
} from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'danger';

export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

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
      icon: 'bd-button--icon',
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
  /**
   * 把样式与交互挂到唯一子元素上(通常是路由 Link / a),渲染为子元素本身。
   * 用于"看起来是按钮、语义上是链接"的场景——不引入额外依赖,内部即最小 Slot。
   */
  asChild?: boolean;
}

/**
 * 最小 Slot:把 props 合并到唯一子元素上并返回该元素。
 * 与 Button 配合时只需处理 className 合并与事件链式调用;
 * disabled / loading 在子元素上无对应属性,由外层用 preventDefault 表达。
 */
function Slot({
  children,
  leading,
  ...slotProps
}: {
  children?: ReactNode;
  /** 渲染在子元素内容之前的节点(加载指示) */
  leading?: ReactNode;
} & Record<string, unknown>) {
  if (!isValidElement(children)) return null;

  const child = children as ReactElement<Record<string, unknown>>;
  const childProps = (child.props ?? {}) as Record<string, unknown>;
  const merged: Record<string, unknown> = { ...slotProps };

  for (const key of Object.keys(childProps)) {
    const parentValue = slotProps[key];
    const childValue = childProps[key];

    if (typeof parentValue === 'function' && typeof childValue === 'function') {
      // 双方都有同名事件处理器时链式调用:先跑父级(可能拦截),再跑子元素自身
      merged[key] = (...args: unknown[]) => {
        (parentValue as (...a: unknown[]) => void)(...args);
        (childValue as (...a: unknown[]) => void)(...args);
      };
    } else {
      // 其余情况子元素优先,父级只补空缺
      merged[key] = childValue ?? parentValue;
    }
  }

  merged.className = cn(
    slotProps.className as string | undefined,
    childProps.className as string | undefined,
  );

  const childChildren = childProps.children as ReactNode;
  if (leading != null) {
    // 用 Fragment 包一层而不是数组:避免给注入的节点强加 key 语义
    merged.children = (
      <>
        {leading}
        {childChildren}
      </>
    );
  }

  return cloneElement(child, merged);
}

export function Button({
  variant = 'primary',
  size = 'md',
  block = false,
  loading = false,
  asChild = false,
  type = 'button',
  className,
  disabled,
  children,
  onClick,
  ...rest
}: ButtonProps) {
  const isDisabled = disabled || loading;
  const classes = cn(buttonVariants({ variant, size, block }), className);
  const spinner = loading ? (
    <span
      data-slot="button-spinner"
      className="bd-button__spinner"
      aria-hidden="true"
    />
  ) : null;

  if (asChild) {
    return (
      <Slot
        data-slot="button"
        data-variant={variant}
        data-size={size}
        aria-busy={loading || undefined}
        aria-disabled={isDisabled || undefined}
        className={classes}
        onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
          // 子元素是链接,没有 disabled 属性:用拦截默认行为表达不可用
          if (isDisabled) event.preventDefault();
          onClick?.(event);
        }}
        leading={spinner}
        {...rest}
      >
        {children}
      </Slot>
    );
  }

  return (
    <button
      type={type}
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={classes}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      onClick={onClick}
      {...rest}
    >
      {spinner}
      {children}
    </button>
  );
}

export { buttonVariants };
