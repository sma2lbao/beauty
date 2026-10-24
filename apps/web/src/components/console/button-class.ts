import { buttonVariants } from '@beauty/design';

import { cn } from '@/lib/utils';

/**
 * 控制台按钮的类名工厂:页面里的 <Link> 也要长得像按钮时复用它。
 *
 * R-26 起视觉只来自 @beauty/design 的 Button 配方(buttonVariants),
 * 本文件只做「控制台变体名 → 组件库变体名」的直通映射,不再自己写颜色/尺寸,
 * 从而 <ConsoleButton> 与类名版 <Link> 永远同一套视觉。
 */
export type ConsoleButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export type ConsoleButtonSize = 'sm' | 'md' | 'lg';

export function consoleButtonClass({
  variant = 'secondary',
  size = 'md',
  className,
}: {
  variant?: ConsoleButtonVariant;
  size?: ConsoleButtonSize;
  className?: string;
} = {}): string {
  return cn(buttonVariants({ variant, size }), className);
}
