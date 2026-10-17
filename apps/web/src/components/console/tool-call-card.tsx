/**
 * 工具调用卡:设计稿工作台里 read_file / edit_file 这类条目的真实数据版。
 *
 * 事件 run.tool.called.v1 只带 name/arguments/ok/detail(见后端 ToolCallRecord),
 * detail 里若是 unified diff 就按增删行渲染,否则按结果文本展示。
 */
import { ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import { useState } from 'react';

import { DiffRows } from '@/components/console/diff-view';
import { summarizeArguments, type ToolCall } from '@/lib/presentation';
import { parseUnifiedDiff } from '@/lib/presentation';
import { cn } from '@/lib/utils';

export function ToolCallCard({
  call,
  live = false,
  defaultOpen = false,
}: {
  call: ToolCall;
  /** 该调用是否仍在运行(run 处于 running 且这是最后一条记录)。 */
  live?: boolean;
  defaultOpen?: boolean;
}) {
  // 默认展开「运行中」的调用;用户手动点过之后以手动状态为准
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? (defaultOpen || live);
  const rows = call.detail ? parseUnifiedDiff(call.detail) : null;
  const status = live ? '运行中' : call.ok ? '完成' : '失败';

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border bg-ha-panel',
        live ? 'border-ha-info-border' : 'border-ha-border',
      )}
    >
      <button
        type="button"
        onClick={() => setOverride(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-ha-panel-2/50"
      >
        {open ? (
          <ChevronDown aria-hidden="true" className="h-3 w-3 shrink-0 text-ha-dim" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3 w-3 shrink-0 text-ha-dim" />
        )}
        <Terminal
          aria-hidden="true"
          className={cn(
            'h-3.5 w-3.5 shrink-0',
            live ? 'text-ha-info' : call.ok ? 'text-ha-muted' : 'text-ha-danger',
          )}
        />
        <span className="shrink-0 font-mono text-[12px] font-medium text-ha-text-body">
          {call.name}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ha-dim">
          {summarizeArguments(call.arguments)}
        </span>
        <span
          className={cn(
            'shrink-0 font-mono text-[11px] font-medium',
            live ? 'text-ha-info' : call.ok ? 'text-ha-success' : 'text-ha-danger',
          )}
        >
          {status}
          {live ? ' · 执行中' : ''}
        </span>
      </button>

      {open && call.detail ? (
        <div className="border-t border-ha-border">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
              结果
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              #{call.seq}
            </span>
          </div>
          {rows ? (
            <DiffRows rows={rows} className="max-h-72 overflow-auto pb-2" />
          ) : (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-[12px] leading-[20px] text-ha-muted">
              {call.detail}
            </pre>
          )}
        </div>
      ) : null}
    </div>
  );
}
