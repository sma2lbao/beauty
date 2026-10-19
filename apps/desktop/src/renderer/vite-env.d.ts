/// <reference types="vite/client" />

import type { DesktopBridge } from '@shared/contract';

declare global {
  interface Window {
    /**
     * 预加载脚本通过 contextBridge 注入的桥接对象。
     * 浏览器直接打开渲染进程时该属性不存在,读取前必须判空(见 lib/bridge.ts)。
     */
    beautyDesktop: DesktopBridge;
  }
}

export {};
