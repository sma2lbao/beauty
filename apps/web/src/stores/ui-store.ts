/**
 * 控制台 UI 偏好(zustand):侧栏显示状态与命令面板开合跨页面保留(后者不落盘);
 * currentConversationId 是页面临时写入的"当前会话"指针(不持久化,刷新即空),
 * 通知 watcher 用它判断"状态变化发生在用户正盯着的会话里",从而只记录不弹 toast。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UiState = {
  sidebarVisible: boolean;
  /** ⌘K 命令面板是否打开(不持久化:刷新后不该自己弹出来)。 */
  commandPaletteOpen: boolean;
  currentConversationId: string | null;
  toggleSidebar: () => void;
  setSidebarVisible: (visible: boolean) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  toggleCommandPalette: () => void;
  setCurrentConversation: (id: string | null) => void;
  reset: () => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarVisible: true,
      commandPaletteOpen: false,
      currentConversationId: null,
      toggleSidebar: () =>
        set((state) => ({ sidebarVisible: !state.sidebarVisible })),
      setSidebarVisible: (visible: boolean) => set({ sidebarVisible: visible }),
      setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
      toggleCommandPalette: () =>
        set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),
      setCurrentConversation: (id) => set({ currentConversationId: id }),
      reset: () => ({
        sidebarVisible: true,
        commandPaletteOpen: false,
        currentConversationId: null,
      }),
    }),
    {
      name: 'beauty-ui-store',
      // 侧栏显隐是用户偏好;命令面板的开合是一次性状态,刷新后不该自己弹出来。
      partialize: (state) => ({ sidebarVisible: state.sidebarVisible }),
    },
  ),
);
