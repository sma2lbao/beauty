/**
 * 子代理面板(P1-E):父 run 用量汇总 + 子代理行(摘要可展开、可就地打开子 run)。
 * 只有存在子代理记录(或父级已出现 subagent 过程项)时由抽屉渲染。
 */
import { Bot, ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { Markdown } from '@/components/console/markdown';
import {
  ConsoleButton,
  ConsolePanel,
  RunStatusBadge,
} from '@/components/console/ui';
import { formatTokens } from '@/lib/presentation';
import type { RunUsage, Subagent } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

import type { RunDetailModel } from './use-run-detail';

export function RunDetailSubagentPanel({
  model,
  onSelectRun,
}: {
  model: RunDetailModel;
  onSelectRun?: (nextRunId: string) => void;
}) {
  const { subagentCalls, subagentTokens, subagents, usageQuery } = model;
  return (
    <ConsolePanel className="p-3" data-testid="run-subagents-panel">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">子代理</span>
        <span className="font-mono text-[11px] text-ha-faint">
          {subagents.length} 个
        </span>
      </div>
      <RunUsageLine usage={usageQuery.data} className="mt-1.5" />
      {subagents.length > 0 ? (
        <>
          <p
            data-testid="run-subagent-total"
            className="mt-0.5 font-mono text-[11px] leading-[17px] text-ha-faint"
          >
            子代理合计:{subagentCalls} 次调用 · {formatTokens(subagentTokens)}{' '}
            tokens
          </p>
          <ul aria-label="子代理列表" className="mt-2 space-y-1.5">
            {subagents.map((subagent) => (
              <SubagentRow
                key={subagent.run_id}
                subagent={subagent}
                onOpenRun={onSelectRun}
              />
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-2 rounded-md border border-dashed border-ha-border px-3 py-2 text-[11px] leading-5 text-ha-dim">
          暂无子代理 run 记录,委派完成后会出现在这里。
        </p>
      )}
    </ConsolePanel>
  );
}

/* ── P1-E:用量汇总与子代理行 ───────────────────────────────────────────── */

/**
 * run 用量汇总(§9):调用次数 + 输入/输出/合计 token + 按模型拆分。
 * 无用量时后端给零值,这里照实显示;面板只在有子代理时出现。
 */
function RunUsageLine({
  usage,
  className,
}: {
  usage: RunUsage | undefined;
  className?: string;
}) {
  if (!usage) return null;
  return (
    <div data-testid="run-usage" className={cn('space-y-1', className)}>
      <p className="font-mono text-[11px] leading-[17px] text-ha-muted">
        本 run 用量:{usage.calls} 次调用 · 输入{' '}
        {formatTokens(usage.prompt_tokens)} · 输出{' '}
        {formatTokens(usage.completion_tokens)} · 合计{' '}
        {formatTokens(usage.total_tokens)} tokens
      </p>
      {usage.by_model.length > 0 ? (
        <ul aria-label="按模型用量" className="space-y-0.5">
          {usage.by_model.map((model) => (
            <li
              key={model.model}
              className="flex items-center justify-between gap-2 font-mono text-[10px] text-ha-faint"
            >
              <span className="min-w-0 truncate">{model.model}</span>
              <span className="shrink-0">
                {model.calls} 次 · {formatTokens(model.total_tokens)} tokens
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** 一个子代理行:名称/标题/状态/用量/摘要(可展开)/打开子 run。 */
function SubagentRow({
  subagent,
  onOpenRun,
}: {
  subagent: Subagent;
  onOpenRun?: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const summary = subagent.summary;
  return (
    <li
      data-testid={`run-subagent-${subagent.run_id}`}
      data-status={subagent.status}
      className="rounded-md border border-ha-border bg-ha-panel px-2.5 py-2"
    >
      <div className="flex items-center gap-2">
        <Bot size={13} className="shrink-0 text-ha-accent" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ha-text-body">
          {subagent.agent_name ?? '子代理'} · {subagent.title}
        </span>
        <RunStatusBadge status={subagent.status} />
      </div>
      <p className="mt-1 font-mono text-[10px] text-ha-faint">
        {subagent.usage.calls} 次调用 ·{' '}
        {formatTokens(subagent.usage.total_tokens)} tokens
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        {summary ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? '收起' : '展开'}子代理「${
              subagent.agent_name ?? '子代理'
            }」摘要`}
            data-testid={`run-subagent-summary-toggle-${subagent.run_id}`}
            onClick={() => setOpen((prev) => !prev)}
            className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 font-mono text-[11px] text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2"
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            摘要
          </button>
        ) : (
          <span className="font-mono text-[10px] text-ha-faint">
            摘要待生成
          </span>
        )}
        {onOpenRun ? (
          <ConsoleButton
            variant="secondary"
            size="sm"
            className="ml-auto"
            data-testid={`run-subagent-open-${subagent.run_id}`}
            onClick={() => onOpenRun(subagent.run_id)}
          >
            打开子 run
          </ConsoleButton>
        ) : null}
      </div>
      {open && summary ? (
        <div
          data-testid={`run-subagent-summary-${subagent.run_id}`}
          className="mt-1.5 max-h-48 overflow-auto rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
        >
          <Markdown content={summary} />
        </div>
      ) : null}
      {subagent.status_reason ? (
        <p className="mt-1 text-[11px] leading-5 text-ha-dim">
          {subagent.status_reason}
        </p>
      ) : null}
    </li>
  );
}
