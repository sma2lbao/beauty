import { beforeEach, describe, expect, it } from 'vitest';

import { resetRunBoard, useRunBoardStore } from './run-board-store';

describe('runBoardStore', () => {
  beforeEach(() => resetRunBoard());

  it('保存选中任务与筛选', () => {
    expect(useRunBoardStore.getState().filter).toBe('all');
    useRunBoardStore.getState().setSelectedRun('run-1');
    useRunBoardStore.getState().setFilter('active');
    expect(useRunBoardStore.getState().selectedRunId).toBe('run-1');
    expect(useRunBoardStore.getState().filter).toBe('active');
    resetRunBoard();
    expect(useRunBoardStore.getState().selectedRunId).toBeNull();
    expect(useRunBoardStore.getState().filter).toBe('all');
  });
});
