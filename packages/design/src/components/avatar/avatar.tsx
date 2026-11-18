import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';

export interface AvatarProps extends Omit<
  ComponentPropsWithoutRef<'span'>,
  'children'
> {
  /** 名字:取首字作为缩写,空值显示「?」 */
  name?: string | null;
  /** 边长(px),默认 28 */
  size?: number;
}

/**
 * 圆形头像:无图片依赖,用名字首字做占位。
 * 取名字而不是取邮箱:邮箱首字会把所有账号都渲染成同一个字母。
 */
export function Avatar({ name, size = 28, className, ...rest }: AvatarProps) {
  const label = name?.trim() ?? '';
  const initial = (label.charAt(0) || '?').toUpperCase();

  return (
    <span
      data-slot="avatar"
      role="img"
      aria-label={label || '未知用户'}
      className={cn('bd-avatar', className)}
      style={{ width: size, height: size }}
      {...rest}
    >
      {initial}
    </span>
  );
}
