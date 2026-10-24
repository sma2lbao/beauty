/**
 * Tailwind 类名合并（实现已收敛到 @beauty/console-ui）。
 *
 * 三端（web / desktop / site）原先各有一份字节相同的 clsx + twMerge 实现，
 * 现统一到共享组件库，这里只做再导出，应用内 '@/lib/utils' 引用零改动。
 */
export { cn } from '@beauty/console-ui';
