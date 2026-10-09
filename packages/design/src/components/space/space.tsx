import type { ComponentPropsWithoutRef, CSSProperties } from 'react';

import { cn } from '../../lib/utils.js';

export type SpaceDirection = 'horizontal' | 'vertical';

export type SpaceAlign = 'start' | 'center' | 'end' | 'baseline';

export type SpaceSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | number;

const SPACE_SIZE: Record<string, number> = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
};

/** 交叉轴对齐 → BEM 修饰类(样式见 space.css) */
const SPACE_ALIGN: Record<SpaceAlign, string> = {
  start: 'bd-space--align-start',
  center: 'bd-space--align-center',
  end: 'bd-space--align-end',
  baseline: 'bd-space--align-baseline',
};

export interface SpaceProps extends ComponentPropsWithoutRef<'div'> {
  /** 排列方向 */
  direction?: SpaceDirection;
  /** 间距,预设档位或自定义像素值 */
  size?: SpaceSize;
  /** 交叉轴对齐方式 */
  align?: SpaceAlign;
  /** 是否换行 */
  wrap?: boolean;
}

export function Space({
  direction = 'horizontal',
  size = 'md',
  align,
  wrap = false,
  style,
  className,
  children,
  ...rest
}: SpaceProps) {
  const gap =
    typeof size === 'number' ? size : (SPACE_SIZE[size] ?? SPACE_SIZE.md);
  const spaceStyle = { gap, ...style } as CSSProperties;

  return (
    <div
      data-slot="space"
      className={cn(
        'bd-space',
        direction === 'vertical' && 'bd-space--vertical',
        align && SPACE_ALIGN[align],
        wrap && 'bd-space--wrap',
        className,
      )}
      style={spaceStyle}
      {...rest}
    >
      {children}
    </div>
  );
}
