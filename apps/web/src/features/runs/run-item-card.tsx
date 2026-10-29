/**
 * 单个运行项卡片:折叠头(图标/标题/版本/用量/耗时/状态)+ 计划正文 + 展开详情。
 *
 * 只负责一条 item 的交互与排版;列表与空态见 ./run-item-timeline.tsx。
 */
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { ConsoleButton, ToneBadge } from '@/components/console/ui';
import {
  RUN_STATUS_LABELS,
  formatDuration,
  formatTokens,
  runStatusTone,
  toneClasses,
} from '@/lib/presentation';
import type { RunItem } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

import { RunItemDetail } from './run-item-detail';
import {
  KIND_META,
  STATUS_META,
  planVersion,
  resolveStatus,
  subagentOf,
} from './run-item-meta';
import { PlanBody } from './run-item-plan-body';

export function RunItemCard({
  item,
  planHistory,
  onOpenRun,
}: {
  item: RunItem;
  planHistory: readonly RunItem[];
  onOpenRun?: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const kind = KIND_META[item.kind];
  const Icon = kind.icon;
  const status = resolveStatus(item);
  const subagent = subagentOf(item);
  // 子代理卡片的状态徽标优先用子 run 的真实状态(父侧投影只知道 running/终态)。
  const childStatus = subagent?.childStatus ?? null;
  const statusMeta = childStatus
    ? {
        label: RUN_STATUS_LABELS[childStatus],
        tone: runStatusTone(childStatus),
      }
    : STATUS_META[status];
  const childRunId = subagent?.childRunId ?? null;
  const usageLabel = subagent?.usage
    ? `${formatTokens(subagent.usage.totalTokens)} tokens`
    : null;
  const version = item.kind === 'plan' ? planVersion(item) : null;
  const duration = formatDuration(item.duration_ms);
  const label = `「${item.title}」`;

  return (
    <li
      data-testid={
        item.kind === 'subagent'
          ? 'run-item-subagent'
          : `run-item-${item.item_id}`
      }
      data-item-id={item.item_id}
      data-kind={item.kind}
      data-status={status}
      className={cn(
        'overflow-hidden rounded-lg border bg-ha-panel',
        status === 'running' ? 'border-ha-info-border' : 'border-ha-border',
      )}
    >
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
          aria-label={`${open ? '收起' : '展开'}${kind.label}${label}详情`}
          data-testid={`run-item-toggle-${item.item_id}`}
          className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-ha-panel-2"
        >
          <span className="shrink-0 text-ha-faint">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <Icon
            size={13}
            className={cn('shrink-0', toneClasses(kind.tone).text)}
            aria-hidden="true"
          />
          <span className="shrink-0 font-mono text-[10px] tracking-[0.14em] text-ha-faint">
            {kind.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ha-text-body">
            {item.title}
          </span>
          {version !== null ? (
            <ToneBadge tone="accent">第 {version} 版</ToneBadge>
          ) : null}
          {usageLabel ? (
            <span className="shrink-0 font-mono text-[10px] text-ha-faint">
              {usageLabel}
            </span>
          ) : null}
          {item.attempt > 1 ? (
            <span className="shrink-0 font-mono text-[10px] text-ha-faint">
              第 {item.attempt} 次尝试
            </span>
          ) : null}
          {duration ? (
            <span className="shrink-0 font-mono text-[11px] text-ha-faint">
              {duration}
            </span>
          ) : null}
          <ToneBadge tone={statusMeta.tone}>{statusMeta.label}</ToneBadge>
        </button>

        {childRunId !== null && status !== 'running' && onOpenRun ? (
          <ConsoleButton
            variant="secondary"
            size="sm"
            data-testid="run-item-subagent-open"
            onClick={() => onOpenRun(childRunId)}
            className="mr-2 shrink-0"
          >
            打开子 run
          </ConsoleButton>
        ) : null}
      </div>

      {item.kind === 'plan' ? (
        <PlanBody
          item={item}
          history={planHistory}
          historyOpen={historyOpen}
          onToggleHistory={() => setHistoryOpen((prev) => !prev)}
        />
      ) : null}

      {open ? <RunItemDetail item={item} /> : null}
    </li>
  );
}
