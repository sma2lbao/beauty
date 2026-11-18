/**
 * Tailwind 类名合并（条件拼接 + 冲突消解）。
 *
 * 三端(web / desktop / site)各保留一份实现,不抽公共包:它只有 4 行,
 * 抽包的代价(多一层 workspace 依赖与发布单元)大于复用收益。
 */
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
