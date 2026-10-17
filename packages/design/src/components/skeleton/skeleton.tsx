import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * Skeleton 骨架屏:内容加载前的占位块。
 *
 * 与 shadcn/ui 的 Skeleton 对齐(单个 div + 脉冲动画),语义类名见同目录 skeleton.css。
 * 尺寸不内置:由使用方通过 className/style 指定宽高。
 */

export type SkeletonProps = ComponentPropsWithoutRef<'div'>;

export function Skeleton({ className, ...rest }: SkeletonProps) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn('bd-skeleton', className)}
      {...rest}
    />
  );
}
