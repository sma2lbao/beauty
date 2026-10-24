import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Tailwind 类名合并:条件拼接 + 冲突消解。
 *
 * 原先 apps/web、apps/desktop(renderer)、apps/site 各有一份字节相同的实现,
 * 现收敛到组件库:三端改为从这里 re-export,不再各自维护。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
