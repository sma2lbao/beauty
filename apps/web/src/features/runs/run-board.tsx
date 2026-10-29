/**
 * 任务看板:空态 / 加载骨架 / 按列渲染任务卡,列与状态映射由 BOARD_COLUMNS 提供。
 */
import { Plus } from 'lucide-react';

import { consoleButtonClass } from '@/components/console/button-class';
import { EmptyPanel } from '@/components/console/ui';
import { BOARD_COLUMNS } from '@/lib/presentation';

import { RunBoardColumn } from './run-board-column';
import type { RunsModel } from './use-runs-model';

export function RunBoard({ model }: { model: RunsModel }) {
  const {
    runs,
    runsQuery,
    selectedRunId,
    setCreateOpen,
    setSelectedRunId,
    visibleColumns,
  } = model;
  return (
    <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto px-6 pb-6 sm:px-8">
      {!runsQuery.isPending && runs.length === 0 ? (
        <EmptyPanel
          className="my-auto w-full border-ha-border-strong"
          title="还没有 run。"
          description="任务编排把每次执行当成一条可观测的运行:先规划、放行后执行、交付前审核。新建一个任务开始。"
          action={
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className={consoleButtonClass({
                variant: 'primary',
                size: 'md',
              })}
            >
              <Plus className="h-3.5 w-3.5" />
              新建任务
            </button>
          }
        />
      ) : null}

      {runsQuery.isPending
        ? BOARD_COLUMNS.map((column) => (
            <div
              key={column.key}
              className="flex min-w-[260px] flex-1 flex-col gap-2.5 rounded-lg border border-ha-border bg-ha-sidebar p-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-ha-text-2">
                  {column.label}
                </span>
                <span className="h-5 w-6 animate-pulse rounded-[5px] bg-ha-panel-2" />
              </div>
              {[0, 1].map((key) => (
                <div
                  key={key}
                  className="h-24 animate-pulse rounded-lg border border-ha-border bg-ha-panel"
                />
              ))}
            </div>
          ))
        : null}

      {!runsQuery.isPending && runs.length > 0
        ? visibleColumns.map((column) => {
            const columnRuns = runs.filter((run) =>
              column.statuses.includes(run.status),
            );
            return (
              <RunBoardColumn
                key={column.key}
                label={column.label}
                tone={column.tone}
                runs={columnRuns}
                selectedRunId={selectedRunId}
                onSelect={setSelectedRunId}
              />
            );
          })
        : null}
    </div>
  );
}
