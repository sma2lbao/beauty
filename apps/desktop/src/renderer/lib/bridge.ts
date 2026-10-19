import type { DesktopBridge } from '@shared/contract';

/**
 * 读取预加载脚本注入的桥接对象。
 *
 * 渲染进程也可能被浏览器直接打开(例如本地调样式),这时 window.beautyDesktop
 * 不存在:调用方必须处理 null(界面给「请通过桌面应用启动」的可读空态),
 * 不能在模块顶层直接解引用,否则整页白屏。
 */
export function getBridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null;
  const bridge: DesktopBridge | undefined = window.beautyDesktop;
  return bridge ?? null;
}
