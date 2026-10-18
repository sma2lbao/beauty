/**
 * 工作台右侧执行面板(384px):执行步骤 / 变更 / 终端。
 *
 * 对应设计稿《01 主控台》右侧面板。数据全部来自 run 事件流:
 * - 执行步骤 = 工具调用记录 + 当前状态;
 * - 变更 = 本次运行的交付物与写操作记录(没有就诚实说没有);
 * - 终端 = 原始事件流水(seq/type),SSE 连接状态在这里显示。
 */
import { Check, Circle, Loader2, Terminal, X } from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';

import { ConsoleTabs, ToneBadge } from '@/components/console/ui';
import {
  RUN_STATUS_LABELS,
  relativeTime,
  runStatusTone,
  summarizeArguments,
  type ToolCall,
} from '@/lib/presentation';
import type { Artifact, Run, RunEvent, RunStatus } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

type PanelTab = 'steps' | 'changes' | 'terminal';

export function ExecutionPanel({
  run,
  status,
  events,
  toolCalls,
  connected,
  reconnecting = false,
  artifact,
  footerAction,
}: {
  run?: Run;
  status: RunStatus;
  events: readonly RunEvent[];
  toolCalls: readonly ToolCall[];
  connected: boolean;
  reconnecting?: boolean;
  artifact?: Artifact | null;
  footerAction?: ReactNode;
}) {
  const [tab, setTab] = useState<PanelTab>('steps');
  const running = status === 'running' || status === 'planning';
  const done = toolCalls.filter((call) => call.ok).length;

  return (
    <aside
      aria-label="执行面板"
      className="hidden w-[384px] shrink-0 flex-col border-l border-ha-border bg-ha-sidebar xl:flex"
    >
      <div className="flex h-11 shrink-0 items-center border-b border-ha-border px-3">
        <ConsoleTabs
          value={tab}
          onChange={setTab}
          items={[
            { value: 'steps', label: '执行步骤' },
            { value: 'changes', label: '变更' },
            { value: 'terminal', label: '终端' },
          ]}
        />
      </div>

      {tab === 'steps' ? (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {toolCalls.length === 0 ? (
            <div className="rounded-md border border-dashed border-ha-border px-4 py-8 text-center text-[12px] leading-5 text-ha-dim">
              {running
                ? '正在整理执行步骤…工具调用会实时出现在这里。'
                : '本次运行还没有工具调用记录。'}
            </div>
          ) : null}

          {toolCalls.map((call, index) => {
            const live = running && index === toolCalls.length - 1 && call.ok;
            const tone = live ? 'info' : call.ok ? 'success' : 'danger';
            return (
              <div
                key={call.seq}
                className={cn(
                  'flex items-start gap-2.5 rounded-md border px-2.5 py-2.5',
                  tone === 'info'
                    ? 'border-ha-info-border bg-ha-info-bg'
                    : 'border-ha-border bg-ha-panel',
                )}
              >
                <span className="mt-0.5 shrink-0">
                  {tone === 'success' ? (
                    <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-success bg-ha-success-bg">
                      <Check className="h-2.5 w-2.5 text-ha-success" />
                    </span>
                  ) : tone === 'info' ? (
                    <span className="grid h-4 w-4 place-items-center">
                      <Loader2 className="h-4 w-4 animate-spin text-ha-info" />
                    </span>
                  ) : (
                    <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-danger bg-ha-danger-bg">
                      <X className="h-2.5 w-2.5 text-ha-danger" />
                    </span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      'block truncate text-[12px] font-medium',
                      tone === 'info' ? 'text-ha-text' : 'text-ha-text-body',
                    )}
                  >
                    {call.name}
                  </span>
                  <span
                    className={cn(
                      'mt-0.5 block truncate font-mono text-[11px]',
                      tone === 'info' ? 'text-ha-info' : 'text-ha-dim',
                    )}
                  >
                    {summarizeArguments(call.arguments) || '无参数'}
                    {' · '}
                    {live ? '运行中' : call.ok ? '完成' : '失败'}
                    {' · #'}
                    {call.seq}
                  </span>
                </span>
              </div>
            );
          })}

          <StatusStep status={status} run={run} />
        </div>
      ) : null}

      {tab === 'changes' ? (
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          <div className="flex items-center justify-between px-0.5">
            <span className="text-[11px] font-semibold text-ha-dim">
              交付物
            </span>
            <ToneBadge tone={artifact ? 'success' : 'neutral'}>
              {artifact ? '1 个' : '0 个'}
            </ToneBadge>
          </div>
          {artifact ? (
            <div className="rounded-md border border-ha-border bg-ha-panel p-3">
              <div className="flex items-center gap-2">
                <span className="truncate text-[12px] font-medium text-ha-text-body">
                  {artifact.title}
                </span>
                <ToneBadge tone="accent">{artifact.kind}</ToneBadge>
              </div>
              <p className="mt-1 font-mono text-[11px] text-ha-dim">
                {relativeTime(artifact.created_at)}
                {' · '}
                {artifact.content.length} 字符
              </p>
              <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-ha-muted">
                {artifact.content.slice(0, 1200)}
                {artifact.content.length > 1200 ? '…' : ''}
              </pre>
            </div>
          ) : (
            <p className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim">
              本次运行还没有交付物。任务完成时会在这里出现。
            </p>
          )}

          <div className="flex items-center justify-between px-0.5 pt-2">
            <span className="text-[11px] font-semibold text-ha-dim">
              工具调用
            </span>
            <ToneBadge tone="neutral">{toolCalls.length} 次</ToneBadge>
          </div>
          {toolCalls.length === 0 ? (
            <p className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] text-ha-dim">
              暂无写操作或读取记录。
            </p>
          ) : (
            <ul className="space-y-1.5">
              {toolCalls.map((call) => (
                <li
                  key={call.seq}
                  className="flex items-center justify-between gap-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2"
                >
                  <span className="truncate font-mono text-[11px] text-ha-text-2">
                    {call.name}
                  </span>
                  <span
                    className={cn(
                      'shrink-0 font-mono text-[11px]',
                      call.ok ? 'text-ha-success' : 'text-ha-danger',
                    )}
                  >
                    {call.ok ? 'ok' : 'failed'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {tab === 'terminal' ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <div className="mb-2 flex items-center justify-between px-0.5">
            <span className="text-[11px] font-semibold text-ha-dim">
              事件流水
            </span>
            <span
              className={cn(
                'inline-flex items-center gap-1.5 font-mono text-[11px]',
                connected
                  ? 'text-ha-success'
                  : reconnecting
                    ? 'text-ha-warning'
                    : 'text-ha-dim',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  connected
                    ? 'animate-pulse bg-ha-success'
                    : reconnecting
                      ? 'animate-pulse bg-ha-warning'
                      : 'bg-ha-ghost',
                )}
              />
              {connected
                ? 'SSE 已连接'
                : reconnecting
                  ? 'SSE 重连中'
                  : 'SSE 未连接'}
            </span>
          </div>
          {events.length === 0 ? (
            <p className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] text-ha-dim">
              还没有事件。
            </p>
          ) : (
            <ol className="space-y-1">
              {events.map((event) => (
                <li
                  key={event.seq}
                  className="flex items-start gap-2 rounded px-1.5 py-1 font-mono text-[11px] hover:bg-ha-panel"
                >
                  <span className="w-8 shrink-0 text-right text-ha-faint">
                    {event.seq}
                  </span>
                  <Terminal
                    aria-hidden="true"
                    className="mt-0.5 h-3 w-3 shrink-0 text-ha-ghost"
                  />
                  <span className="min-w-0 flex-1 break-all text-ha-muted">
                    {event.type}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}

      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-t border-ha-border px-3">
        <span className="truncate font-mono text-[11px] text-ha-dim">
          {running
            ? `已执行 ${done} 步 · 进行中`
            : `已执行 ${done} 步 · ${RUN_STATUS_LABELS[status]}`}
        </span>
        {footerAction}
      </div>
    </aside>
  );
}

function StatusStep({ status, run }: { status: RunStatus; run?: Run }) {
  const tone = runStatusTone(status);
  const label = RUN_STATUS_LABELS[status];
  const description =
    status === 'awaiting_approval'
      ? '等待你放行计划'
      : status === 'awaiting_confirmation'
        ? '等待你确认写操作'
        : status === 'blocked'
          ? '任务中断,需要人工处理'
          : status === 'failed'
            ? '执行失败'
            : status === 'completed'
              ? '任务完成'
              : status === 'running'
                ? 'Agent 正在执行下一步'
                : '等待规划';
  return (
    <div
      className={cn(
        'flex items-start gap-2.5 rounded-md border px-2.5 py-2.5',
        tone === 'info'
          ? 'border-ha-info-border bg-ha-info-bg'
          : 'border-ha-border bg-ha-panel',
      )}
    >
      <span className="mt-0.5 shrink-0">
        {status === 'running' || status === 'planning' ? (
          <Loader2 className="h-4 w-4 animate-spin text-ha-info" />
        ) : status === 'completed' ? (
          <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-success bg-ha-success-bg">
            <Check className="h-2.5 w-2.5 text-ha-success" />
          </span>
        ) : status === 'failed' || status === 'blocked' ? (
          <span className="grid h-4 w-4 place-items-center rounded-full border border-ha-danger bg-ha-danger-bg">
            <X className="h-2.5 w-2.5 text-ha-danger" />
          </span>
        ) : (
          <Circle aria-hidden="true" className="h-4 w-4 text-ha-ghost" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            'block truncate text-[12px] font-medium',
            status === 'running' || status === 'planning'
              ? 'text-ha-text'
              : 'text-ha-text-2',
          )}
        >
          {label}
        </span>
        <span
          className={cn(
            'mt-0.5 block truncate font-mono text-[11px]',
            status === 'running' || status === 'planning'
              ? 'text-ha-info'
              : 'text-ha-dim',
          )}
        >
          {description}
          {run?.rounds ? ` · 第 ${run.rounds} 轮` : ''}
        </span>
      </span>
    </div>
  );
}
