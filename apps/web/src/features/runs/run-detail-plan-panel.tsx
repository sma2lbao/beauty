/**
 * 计划面板:审批阶段可编辑(Markdown 草稿 + 保存),其余状态只读渲染。
 */
import { Markdown } from '@/components/console/markdown';
import { ConsoleButton, ConsolePanel } from '@/components/console/ui';
import { cn } from '@/lib/utils';

import { CONSOLE_TEXTAREA } from './run-detail-styles';
import type { RunDetailModel } from './use-run-detail';

export function RunDetailPlanPanel({ model }: { model: RunDetailModel }) {
  const { editable, plan, planDraft, savePlan, setPlanDraft } = model;
  return (
    <ConsolePanel className="p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">计划</span>
        <span className="font-mono text-[11px] text-ha-faint">
          {editable ? '可编辑' : '只读'}
        </span>
      </div>
      {editable ? (
        <textarea
          aria-label="计划内容"
          rows={10}
          value={planDraft ?? plan?.plan_markdown ?? ''}
          onChange={(event) => setPlanDraft(event.target.value)}
          className={cn(CONSOLE_TEXTAREA, 'mt-2 font-mono text-[11px]')}
        />
      ) : (
        <div className="mt-2 max-h-72 overflow-auto rounded-md border border-ha-border bg-ha-panel px-3 py-2">
          <Markdown content={plan?.plan_markdown ?? ''} />
        </div>
      )}
      {editable ? (
        <div className="mt-2 flex justify-end">
          <ConsoleButton
            variant="secondary"
            size="sm"
            onClick={() => savePlan.mutate()}
            disabled={savePlan.isPending}
          >
            保存计划
          </ConsoleButton>
        </div>
      ) : null}
    </ConsolePanel>
  );
}
