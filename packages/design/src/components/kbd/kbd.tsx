import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * 键盘按键提示。用来标注快捷键(⌘K / Esc),不承载业务语义;
 * 内容是按键本身,读屏会读成「K 键」这类文本,不需要额外 aria。
 */
export function Kbd({
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<'kbd'>) {
  return (
    <kbd data-slot="kbd" className={cn('bd-kbd', className)} {...rest}>
      {children}
    </kbd>
  );
}
