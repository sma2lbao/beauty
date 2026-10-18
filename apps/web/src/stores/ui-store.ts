/**
 * 控制台 UI 偏好(zustand):侧栏显示状态跨页面/刷新保留;
 * currentConversationId 是页面临时写入的"当前会话"指针(不持久化,刷新即空),
 * 通知 watcher 用它判断"状态变化发生在用户正盯着的会话里",从而只记录不弹 toast。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UiState = {
  sidebarVisible: boolean;
  currentConversationId: string | null;
  toggleSidebar: () => void;
  setSidebarVisible: (visible: boolean) => void;
  setCurrentConversation: (id: string | null) => void;
  reset: () => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarVisible: true,
      currentConversationId: null,
      toggleSidebar: () =>
        set((state) => ({ sidebarVisible: !state.sidebarVisible })),
      setSidebarVisible: (visible: boolean) =>
        set({ sidebarVisible: visible }),
      setCurrentConversation: (id) => set({ currentConversationId: id }),
      reset: () => ({
        sidebarVisible: true,
        currentConversationId: null,
      }),
    }),
    {
      name: 'beauty-ui-store',
      partialize: (state) => ({ sidebarVisible: state.sidebarVisible }),
    },
  ),
);
