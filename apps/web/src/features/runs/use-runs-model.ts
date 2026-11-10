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

/** 超时上限:7 天,与后端 RunCreate.timeout_seconds 的 le=604800 对齐。 */
export const RUN_TIMEOUT_MAX_SECONDS = 604800;
export const RUN_PRIORITY_MIN = -100;
export const RUN_PRIORITY_MAX = 100;

/**
 * 新建任务的本地校验(P1-C):留空 = 用平台默认;超时只接受整数秒,
 * 优先级只接受 -100..100 整数。返回中文错误文案,null 表示通过。
 */
export function validateRunCreate(input: {
  timeoutSeconds: string;
  priority: string;
}): string | null {
  const timeout = input.timeoutSeconds.trim();
  if (timeout) {
    if (!/^\d+$/.test(timeout)) return '超时必须是不小于 0 的整数秒。';
    if (Number(timeout) > RUN_TIMEOUT_MAX_SECONDS) {
      return '超时不能超过 604800 秒(7 天)。';
    }
  }
  const priority = input.priority.trim();
  if (priority) {
    if (!/^-?\d+$/.test(priority)) return '优先级必须是 -100..100 的整数。';
    const value = Number(priority);
    if (value < RUN_PRIORITY_MIN || value > RUN_PRIORITY_MAX) {
      return '优先级必须是 -100..100 的整数。';
    }
  }
  return null;
}

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
  const [timeoutSeconds, setTimeoutSeconds] = useState('');
  const [priority, setPriority] = useState('0');
  const [createError, setCreateError] = useState<string | null>(null);

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
        // 留空即交给平台默认:键直接省略,避免把「未填」变成显式 0(不限)。
        ...(timeoutSeconds.trim()
          ? { timeout_seconds: Number(timeoutSeconds.trim()) }
          : {}),
        priority: priority.trim() ? Number(priority.trim()) : 0,
      }),
    onSuccess: async (run) => {
      setCreateError(null);
      setSelectedRunId(run.id);
      setCreateOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });

  /** 先本地校验再提交:非法输入不发请求,错误就地显示在弹窗里。 */
  const submitCreate = () => {
    const message = validateRunCreate({ timeoutSeconds, priority });
    if (message) {
      setCreateError(message);
      return;
    }
    setCreateError(null);
    createMutation.mutate();
  };

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
    timeoutSeconds,
    setTimeoutSeconds,
    priority,
    setPriority,
    createError,
    setCreateError,
    submitCreate,
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
