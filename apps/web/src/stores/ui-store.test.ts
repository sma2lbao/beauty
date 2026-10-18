import { beforeEach, describe, expect, it } from 'vitest';

import { useUiStore } from './ui-store';

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.getState().reset();
    localStorage.clear();
  });

  it('默认显示侧栏,可切换并持久化', () => {
    expect(useUiStore.getState().sidebarVisible).toBe(true);
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarVisible).toBe(false);
    expect(localStorage.getItem('beauty-ui-store')).toContain(
      '"sidebarVisible":false',
    );
    useUiStore.getState().setSidebarVisible(true);
    expect(useUiStore.getState().sidebarVisible).toBe(true);
  });
});
