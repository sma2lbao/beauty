/**
 * 对话页的纯函数:放在这里而不是页面文件里,便于单测,也避免
 * `react-refresh/only-export-components`(页面文件只导出组件)。
 */

/** 用输入里的第一行做任务标题(过长截断,避免列表被撑爆)。 */
export function titleFromMessage(text: string): string {
  const firstLine = text.trim().split('\n')[0]?.trim() ?? '';
  const title = firstLine || '新任务';
  return title.length > 40 ? `${title.slice(0, 40)}…` : title;
}
