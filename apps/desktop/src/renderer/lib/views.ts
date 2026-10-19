/** 桌面应用内的顶层视图(本地模式):工作台 / 文件 / 设置。 */
export type DesktopView = 'chat' | 'files' | 'settings';

export const VIEW_LABELS: Record<DesktopView, string> = {
  chat: '工作台',
  files: '文件',
  settings: '设置',
};
