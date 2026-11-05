/**
 * ⌘K / Ctrl+K 全局快捷键。
 *
 * 监听挂在 window 而不是某个容器上:面板关闭时触发元素可能在任何页面,
 * 打开时焦点又在面板输入框里 —— 只有 window 能同时覆盖两种情况。
 * 因此这里**不**按 event.target 过滤输入框,否则在输入框内按 ⌘K 就关不掉面板。
 */
import { useEffect } from 'react';

import { useUiStore } from '@/stores/ui-store';

export function useCommandPaletteShortcut(): void {
  const toggleCommandPalette = useUiStore(
    (state) => state.toggleCommandPalette,
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)) {
        // 不拦截的话 Chromium 的「聚焦地址栏搜索」会抢走按键。
        event.preventDefault();
        toggleCommandPalette();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [toggleCommandPalette]);
}
