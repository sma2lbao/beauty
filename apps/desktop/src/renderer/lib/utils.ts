import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Tailwind 类名合并:条件拼接 + 冲突消解(与 apps/web 的 cn 一致)。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
