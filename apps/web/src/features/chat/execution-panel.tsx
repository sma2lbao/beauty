/**
 * 工作台右侧执行面板(384px):执行步骤 / 文件 / 终端。
 *
 * 对应设计稿《01 主控台》右侧面板。数据来源分两路:
 * - 执行步骤 = run 事件流里的工具调用 + 当前状态;
 * - 文件 / 终端 = 会话沙箱(workspace-service),沙箱按会话 scope_key 查,
 *   没有就诚实说没有(不是所有会话都跑过工具)。
 *
 * 事件流水已下线:SSE 连接状态在底部状态栏,这里只留人能直接用的东西。
 */
import { Check, Circle, Loader2, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { SandboxFileTree } from '@/features/sandbox/sandbox-file-tree';
import { SandboxTerminal } from '@/features/sandbox/sandbox-terminal';
import { ConsoleButton, ConsoleTabs, ToneBadge } from '@/components/console/ui';
import { RunStepTimeline } from '@/features/runs/run-step-timeline';
import { RunTodoList } from '@/features/runs/run-todo-list';
import {
  RUN_STATUS_LABELS,
  runStatusTone,
  summarizeArguments,
  type ToolCall,
} from '@/lib/presentation';
import type { Run, RunStatus, RunStep, RunTodoItem } from '@/lib/runs-api';
import type { SandboxInfo } from '@/lib/sandbox-api';
import { cn } from '@/lib/utils';

type PanelTab = 'steps' | 'files' | 'terminal';

export function ExecutionPanel({
  run,
  status,
  toolCalls,
  steps = [],
  todos = [],
  footerAction,
  sandbox = null,
  sandboxLoading = false,
  sandboxError = null,
  onRetrySandbox,
  onOpenFile,
  selectedFilePath = null,
  conversationId,
}: {
  run?: Run;
  status: RunStatus;
  toolCalls: readonly ToolCall[];
  /** 节点级步骤投影(§6.3):比工具调用粗一级,带尝试/轮次/耗时。 */
  steps?: readonly RunStep[];
  /** 任务清单投影:空数组表示没有清单,整块不渲染。 */
  todos?: readonly RunTodoItem[];
  /** 交付物统一走 footer 的 RunFooterAction;面板不再单开变更页签。 */
  footerAction?: ReactNode;
  sandbox?: SandboxInfo | null;
  sandboxLoading?: boolean;
  sandboxError?: Error | null;
  onRetrySandbox?: () => void;
  /** 点文件树里的文件时打开查看器。 */
  onOpenFile?: (path: string) => void;
  /** 查看器当前打开的文件(树里高亮)。 */
  selectedFilePath?: string | null;
  /** 当前会话 id:文件树/文件查看器按会话取补丁时需要(可选,缺失时走本地数据)。 */
  conversationId?: string;
}) {
  const [tab, setTab] = useState<PanelTab>('steps');
  const running = status === 'running' || status === 'planning';
  const done = toolCalls.filter((call) => call.ok).length;
  const touchedPaths = useMemo(
    () => toolCalls.flatMap((call) => call.paths ?? []),
    [toolCalls],
  );

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
            { value: 'files', label: '文件' },
            { value: 'terminal', label: '终端' },
          ]}
        />
      </div>

      {tab === 'steps' ? (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {steps.length > 0 ? (
            <>
              <div className="flex items-center justify-between px-0.5">
                <span className="text-[11px] font-semibold text-ha-dim">
                  节点步骤
                </span>
                <ToneBadge tone="neutral">{steps.length} 个</ToneBadge>
              </div>
              <RunStepTimeline steps={steps} />
            </>
          ) : null}

          <RunTodoList todos={todos} />

          {steps.length === 0 && toolCalls.length === 0 ? (
            <div className="rounded-md border border-dashed border-ha-border px-4 py-8 text-center text-[12px] leading-5 text-ha-dim">
              {running
                ? '正在整理执行步骤…工具调用会实时出现在这里。'
                : '本次运行还没有工具调用记录。'}
            </div>
          ) : null}

          {toolCalls.length > 0 ? (
            <div className="flex items-center justify-between px-0.5 pt-1">
              <span className="text-[11px] font-semibold text-ha-dim">
                工具调用
              </span>
              <ToneBadge tone="neutral">{toolCalls.length} 次</ToneBadge>
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

      {tab === 'files' ? (
        <div className="flex min-h-0 flex-1 flex-col p-3">
          <SandboxGate
            sandbox={sandbox}
            loading={sandboxLoading}
            error={sandboxError}
            onRetry={onRetrySandbox}
          >
            {(active) => (
              <SandboxFileTree
                key={active.id}
                sandboxId={active.id}
                conversationId={conversationId}
                touchedPaths={touchedPaths}
                selectedPath={selectedFilePath}
                onSelect={(path) => onOpenFile?.(path)}
              />
            )}
          </SandboxGate>
        </div>
      ) : null}

      {tab === 'terminal' ? (
        <div className="flex min-h-0 flex-1 flex-col p-3">
          <SandboxGate
            sandbox={sandbox}
            loading={sandboxLoading}
            error={sandboxError}
            onRetry={onRetrySandbox}
          >
            {(active) => (
              <SandboxTerminal key={active.id} sandboxId={active.id} />
            )}
          </SandboxGate>
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

/**
 * 沙箱三态闸门:加载中 / 失败(可重试) / 没有沙箱。
 * 只有拿到运行中的沙箱才渲染 children(文件树、终端)。
 */
function SandboxGate({
  sandbox,
  loading,
  error,
  onRetry,
  children,
}: {
  sandbox: SandboxInfo | null;
  loading: boolean;
  error: Error | null;
  onRetry?: () => void;
  children: (sandbox: SandboxInfo) => ReactNode;
}) {
  if (sandbox) return <>{children(sandbox)}</>;
  if (loading) {
    return (
      <p className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim">
        正在查找会话沙箱…
      </p>
    );
  }
  if (error) {
    return (
      <div className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center">
        <p className="text-[12px] leading-5 text-ha-danger">
          {error.message || '沙箱信息读取失败'}
        </p>
        {onRetry ? (
          <ConsoleButton
            size="sm"
            variant="secondary"
            className="mt-2"
            onClick={onRetry}
          >
            重试
          </ConsoleButton>
        ) : null}
      </div>
    );
  }
  return (
    <p className="rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim">
      这个会话还没有运行中的沙箱。
      <span className="mt-1 block text-[11px] text-ha-faint">
        Agent 在沙箱里跑过工具后，文件与终端会出现在这里。
      </span>
    </p>
  );
}

function StatusStep({ status, run }: { status: RunStatus; run?: Run }) {
  const tone = runStatusTone(status);
  const label = RUN_STATUS_LABELS[status];
  const description =
    status === 'awaiting_approval'
      ? '等待你放行计划'
      : status === 'awaiting_confirmation'
        ? '等待你确认危险操作'
        : status === 'awaiting_input'
          ? '等待你回答提问'
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
