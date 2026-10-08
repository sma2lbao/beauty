/**
 * 轻量类名拼接工具,用于把多个类名合并为一个字符串。
 *
 * - 忽略 `null` / `undefined` / `false` / 空字符串等假值
 * - 支持嵌套数组,方便条件组合
 */
export type ClassValue =
  | ClassValue[]
  | string
  | number
  | null
  | undefined
  | false;

export function cx(...values: ClassValue[]): string {
  const classes: string[] = [];

  for (const value of values) {
    if (!value) continue;

    if (Array.isArray(value)) {
      const nested = cx(...value);
      if (nested) classes.push(nested);
    } else {
      classes.push(String(value));
    }
  }

  return classes.join(' ');
}
