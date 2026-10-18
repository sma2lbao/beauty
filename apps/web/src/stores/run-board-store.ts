/**
 * 任务看板状态(zustand):选中任务与筛选在页面切换后保留。
 */
import { create } from 'zustand';

export type RunFilterKey = 'all' | 'active' | 'review';

type RunBoardState = {
  selectedRunId: string | null;
  filter: RunFilterKey;
  setSelectedRun: (id: string | null) => void;
  setFilter: (filter: RunFilterKey) => void;
  reset: () => void;
};

export const useRunBoardStore = create<RunBoardState>((set) => ({
  selectedRunId: null,
  filter: 'all',
  setSelectedRun: (selectedRunId) => set({ selectedRunId }),
  setFilter: (filter) => set({ filter }),
  reset: () => set({ selectedRunId: null, filter: 'all' }),
}));

export const resetRunBoard = () => useRunBoardStore.getState().reset();
