const MONTHS = [
  '一月',
  '二月',
  '三月',
  '四月',
  '五月',
  '六月',
  '七月',
  '八月',
  '九月',
  '十月',
  '十一月',
  '十二月',
];

/** 列表里的紧凑日期,保留等宽排版的对齐感。 */
export function formatDate(input: string): string {
  return input.replaceAll('-', '.');
}

/** 详情页的长日期,给中文阅读一点呼吸。 */
export function formatDateLong(input: string): string {
  const [year, month, day] = input.split('-').map(Number);
  return `${year} 年 ${MONTHS[month - 1]} ${day} 日`;
}
