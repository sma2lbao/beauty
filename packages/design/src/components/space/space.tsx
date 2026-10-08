import type { ComponentPropsWithoutRef, CSSProperties } from 'react';

import { cx } from '../../utils/class-name';

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
    typeof size === 'number' ? size : SPACE_SIZE[size] ?? SPACE_SIZE.md;
  const spaceStyle = { gap, ...style } as CSSProperties;

  return (
    <div
      className={cx(
        'bd-space',
        `bd-space--${direction}`,
        align && `bd-space--align-${align}`,
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
