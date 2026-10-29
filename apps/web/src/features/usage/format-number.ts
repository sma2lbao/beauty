/**
 * 用量页数字格式化:千分位(zh-CN)。
 */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value);
}
