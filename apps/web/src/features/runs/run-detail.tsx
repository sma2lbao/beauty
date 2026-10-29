/**
 * 运行详情抽屉:任务编排看板点开一张卡后出现。
 *
 * 能力与原详情一致(审批计划、确认危险操作、resume、retry、追加指令、取交付物、
 * 看事件流),只是换成控制台的暗色面板语言;关键可访问名/testid 保留。
 *
 * 拆分后本文件只做抽屉骨架与组装:数据/变更见 ./use-run-detail.ts,
 * 摘要/操作/计划/子代理/追加指令/事件流见 ./run-detail-*.tsx。
 */
import { X } from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';
import { RunArtifacts } from '@/features/runs/run-artifacts';
import { RunItemTimeline } from '@/features/runs/run-item-timeline';
import { RunStepTimeline } from '@/features/runs/run-step-timeline';
import { RunTodoList } from '@/features/runs/run-todo-list';

import { RunDetailActions } from './run-detail-actions';
import { RunDetailEventsPanel } from './run-detail-events-panel';
import { RunDetailInstructionsPanel } from './run-detail-instructions';
import { RunDetailPlanPanel } from './run-detail-plan-panel';
import { RunDetailSubagentPanel } from './run-detail-subagent-panel';
import { RunDetailSummary } from './run-detail-summary';
import { useRunDetail } from './use-run-detail';

export function RunDetail({
  runId,
  onSelectRun,
  onClose,
}: {
  runId: string;
  onSelectRun?: (nextRunId: string) => void;
  onClose: () => void;
}) {
  const model = useRunDetail({ runId, onSelectRun });
  const {
    itemsQuery,
    run,
    showArtifact,
    showSubagents,
    stepsQuery,
    todosQuery,
  } = model;

  return (
    <aside
      aria-label="运行详情"
      className="flex h-full w-full flex-col border-l border-ha-border bg-ha-sidebar sm:w-[460px]"
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-ha-border px-4">
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ha-text">
          {run ? run.title : '运行详情'}
        </span>
        <button
          type="button"
          aria-label="关闭详情"
          onClick={onClose}
          className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <section>
          <RunDetailSummary model={model} onSelectRun={onSelectRun} />
          <RunDetailActions model={model} />
        </section>

        <RunDetailPlanPanel model={model} />

        <ConsolePanel className="p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-semibold text-ha-text-2">
              运行项
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              {itemsQuery.data?.length ?? 0} 项
            </span>
          </div>
          <RunItemTimeline
            items={itemsQuery.data ?? []}
            className="mt-2 max-h-96 overflow-y-auto"
            onOpenRun={onSelectRun}
          />
        </ConsolePanel>

        {showSubagents ? (
          <RunDetailSubagentPanel model={model} onSelectRun={onSelectRun} />
        ) : null}

        <ConsolePanel className="p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-semibold text-ha-text-2">
              执行步骤
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              {stepsQuery.data?.length ?? 0} 个节点
            </span>
          </div>
          <RunStepTimeline
            steps={stepsQuery.data ?? []}
            className="mt-2 max-h-72 overflow-y-auto"
          />
          <RunTodoList todos={todosQuery.data?.todos ?? []} className="mt-3" />
        </ConsolePanel>

        <RunDetailInstructionsPanel model={model} />

        {showArtifact ? <RunArtifacts runId={runId} /> : null}

        <RunDetailEventsPanel
          events={model.events}
          connected={model.connected}
        />
      </div>
    </aside>
  );
}
