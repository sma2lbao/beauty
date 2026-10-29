/**
 * 任务编排看板的数据与视图状态:run 列表(轮询 5s)、新建任务变更、
 * 筛选/新建弹窗开关与草稿字段,以及按列分组的派生结果。
 * 视图组件只消费这里,不再各自发请求。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { BOARD_COLUMNS, isRunQuiescent } from '@/lib/presentation';
import { isUnauthorized, runsApi } from '@/lib/runs-api';
import { useRunBoardStore } from '@/stores/run-board-store';
import { queryKeys } from '@/lib/query-keys';

export function useRunsModel() {
  const queryClient = useQueryClient();
  const selectedRunId = useRunBoardStore((state) => state.selectedRunId);
  const setSelectedRunId = useRunBoardStore((state) => state.setSelectedRun);
  const filter = useRunBoardStore((state) => state.filter);
  const setFilter = useRunBoardStore((state) => state.setFilter);
  const [createOpen, setCreateOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [title, setTitle] = useState('整理本周文档成周报');
  const [goal, setGoal] = useState('汇总本周文档,生成周报草稿');
  const [criteria, setCriteria] = useState('覆盖本周主要进展\n不超过 300 字');

  const runsQuery = useQuery({
    queryKey: queryKeys.runs,
    queryFn: runsApi.list,
    refetchInterval: 5000,
  });
  const createMutation = useMutation({
    mutationFn: () =>
      runsApi.create({
        title: title.trim(),
        goal: goal.trim(),
        acceptance_criteria: criteria
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean),
      }),
    onSuccess: async (run) => {
      setSelectedRunId(run.id);
      setCreateOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });

  const runs = runsQuery.data ?? [];
  const activeCount = runs.filter((run) => !isRunQuiescent(run.status)).length;
  const runningCount = runs.filter((run) => run.status === 'running').length;
  const unauthorized = isUnauthorized(runsQuery.error);

  const visibleColumns = BOARD_COLUMNS.filter((column) => {
    if (filter === 'active') {
      return column.key === 'queued' || column.key === 'running';
    }
    if (filter === 'review') return column.key === 'review';
    return true;
  });

  const subtitle = runsQuery.isPending
    ? '正在读取任务…'
    : activeCount +
      ' 个活跃任务 · ' +
      runningCount +
      ' 个执行中 · 共 ' +
      runs.length +
      ' 个任务';

  return {
    selectedRunId,
    setSelectedRunId,
    filter,
    setFilter,
    createOpen,
    setCreateOpen,
    filterOpen,
    setFilterOpen,
    title,
    setTitle,
    goal,
    setGoal,
    criteria,
    setCriteria,
    runsQuery,
    createMutation,
    runs,
    activeCount,
    runningCount,
    unauthorized,
    visibleColumns,
    subtitle,
  };
}

export type RunsModel = ReturnType<typeof useRunsModel>;
