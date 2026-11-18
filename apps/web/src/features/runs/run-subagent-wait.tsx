/**
 * 子代理等待清单(L2 异步子代理 §2 第 26 条):父 run 停在 `awaiting_subagent` 时,
 * 告诉人「在等谁、谁卡在需要你处理的地方」。
 *
 * 异步模式下子 run 有 `conversation_id=None`,**不出现在会话里**;父 run 的执行面板
 * 只会显示「等待子代理」四个字。子 run 停在 pending/running 时只是等,但停在
 * `awaiting_approval`/`awaiting_confirmation`/`awaiting_input` 时是**等人**——
 * 这时要显式指出来,否则父 run 看起来像挂了。
 */
import { ArrowUpRight, Bot } from 'lucide-react';
import { Link } from 'react-router';

import { RunStatusBadge, ToneBadge } from '@/components/console/ui';
import type { Subagent } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

/** 子 run 停在需要人工介入的状态:等人,不是卡住。 */
const HUMAN_WAIT_HINTS: Partial<Record<Subagent['status'], string>> = {
  awaiting_approval: '子代理待你审批计划',
  awaiting_confirmation: '子代理待你确认危险操作',
  awaiting_input: '子代理在等你回答提问',
};

export function SubagentWaitList({
  subagents,
}: {
  subagents: readonly Subagent[];
}) {
  return (
    <section
      aria-label="等待子代理"
      data-testid="chat-subagent-wait"
      className="rounded-md border border-ha-info-border bg-ha-info-bg px-2.5 py-2.5"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">
          等待子代理
        </span>
        <ToneBadge tone="info">{subagents.length} 个</ToneBadge>
      </div>
      {subagents.length === 0 ? (
        <p className="mt-1.5 text-[11px] leading-5 text-ha-dim">
          子代理刚入队，状态马上会出现在这里。
        </p>
      ) : (
        <ul aria-label="等待中的子代理列表" className="mt-1.5 space-y-1.5">
          {subagents.map((subagent) => {
            const hint = HUMAN_WAIT_HINTS[subagent.status];
            return (
              <li
                key={subagent.run_id}
                data-testid={`chat-subagent-${subagent.run_id}`}
                data-status={subagent.status}
                className="rounded-md border border-ha-border bg-ha-panel px-2.5 py-1.5"
              >
                <div className="flex items-center gap-2">
                  <Bot
                    size={13}
                    className="shrink-0 text-ha-accent"
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ha-text-body">
                    {subagent.agent_name ?? '子代理'} · {subagent.title}
                  </span>
                  <RunStatusBadge status={subagent.status} />
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-[11px] leading-5',
                      hint ? 'text-ha-warning' : 'text-ha-faint',
                    )}
                  >
                    {hint ??
                      subagent.status_reason ??
                      '子代理在执行中，结束后父任务自动继续'}
                  </span>
                  <Link
                    to={`/runs?run=${subagent.run_id}`}
                    aria-label={`打开子代理「${subagent.title}」`}
                    className="inline-flex shrink-0 items-center gap-0.5 font-mono text-[11px] text-ha-info hover:underline"
                  >
                    打开
                    <ArrowUpRight size={11} aria-hidden="true" />
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
