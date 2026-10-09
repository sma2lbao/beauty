import { clsx, type ClassValue } from 'clsx';

/**
 * 类名拼接工具。
 *
 * 组件的样式由语义类名 + 普通 CSS 提供(见各组件同目录的 .css),
 * 不再依赖 Tailwind 工具类,因此无需 tailwind-merge 去合并冲突的原子类;
 * clsx 负责条件拼接:忽略假值、支持数组与对象。
 *
 * cn('bd-button', isPrimary && 'bd-button--primary') // 'bd-button bd-button--primary'
 */
export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

/** 兼容旧版 cx 的类名拼接 API,行为与 clsx 一致。 */
export { clsx as cx };
export type { ClassValue };
