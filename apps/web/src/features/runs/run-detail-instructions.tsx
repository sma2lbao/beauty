/**
 * 追加指令面板(§6.5):排队后在下一个人工边界并入执行;终态不可提交,
 * blocked 仍可排队(resume 时消费)。
 */
import {
  ConsoleButton,
  ConsolePanel,
  ToneBadge,
} from '@/components/console/ui';
import { cn } from '@/lib/utils';

import { CONSOLE_TEXTAREA } from './run-detail-styles';
import type { RunDetailModel } from './use-run-detail';

export function RunDetailInstructionsPanel({
  model,
}: {
  model: RunDetailModel;
}) {
  const {
    instruction,
    instructionsQuery,
    queueInstruction,
    run,
    setInstruction,
    status,
  } = model;
  return (
    <ConsolePanel className="p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">
          追加指令
        </span>
        <span className="font-mono text-[11px] text-ha-faint">§6.5</span>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-ha-dim">
        排队后在下一个人工边界(规划/放行/确认)并入执行。
      </p>
      <textarea
        aria-label="追加指令"
        rows={2}
        placeholder="例如:顺便把文档要点也汇总一下"
        value={instruction}
        onChange={(event) => setInstruction(event.target.value)}
        className={cn(CONSOLE_TEXTAREA, 'mt-2')}
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <ConsoleButton
          variant="secondary"
          size="sm"
          onClick={() => queueInstruction.mutate()}
          disabled={
            queueInstruction.isPending ||
            !instruction.trim() ||
            !run ||
            // 后端只拒绝 completed/failed/cancelled;blocked 仍可排队(等 resume 时消费)
            status === 'completed' ||
            status === 'failed' ||
            status === 'cancelled'
          }
        >
          提交指令
        </ConsoleButton>
        {queueInstruction.isError ? (
          <span className="text-[12px] text-ha-danger">
            {queueInstruction.error.message}
          </span>
        ) : null}
      </div>
      <ul className="mt-2 space-y-1.5">
        {(instructionsQuery.data ?? []).map((item) => (
          <li
            key={item.id}
            className="flex items-center justify-between gap-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2"
          >
            <span className="truncate text-[12px] text-ha-text-2">
              {item.content}
            </span>
            <ToneBadge tone={item.status === 'queued' ? 'warning' : 'success'}>
              {item.status === 'queued' ? '待消费' : '已消费'}
            </ToneBadge>
          </li>
        ))}
      </ul>
    </ConsolePanel>
  );
}
