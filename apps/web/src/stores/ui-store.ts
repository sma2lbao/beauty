/**
 * 控制台 UI 偏好(zustand):侧栏显示状态跨页面/刷新保留。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UiState = {
  sidebarVisible: boolean;
  toggleSidebar: () => void;
  setSidebarVisible: (visible: boolean) => void;
  reset: () => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarVisible: true,
      toggleSidebar: () =>
        set((state) => ({ sidebarVisible: !state.sidebarVisible })),
      setSidebarVisible: (visible) => set({ sidebarVisible: visible }),
      reset: () => set({ sidebarVisible: true }),
    }),
    {
      name: 'beauty-ui-store',
      partialize: (state) => ({ sidebarVisible: state.sidebarVisible }),
    },
  ),
);
