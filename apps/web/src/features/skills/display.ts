/**
 * Skills 页展示格式化:纯函数,不含 React 依赖。
 */

/** 本地化日期(zh-CN);非法字符串原样返回。 */
export function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString('zh-CN');
}
