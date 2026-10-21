/**
 * 运行详情抽屉:任务编排看板点开一张卡后出现。
 *
 * 能力与原 /runs 详情一致(审批计划、确认危险操作、resume、retry、追加指令、
 * 取交付物、看事件流),只是换成控制台的暗色面板语言。
 * 关键可访问名/testid 保留,便于既有行为测试钉住。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { useState } from 'react';

import {
  ConsoleButton,
  ConsolePanel,
  RunStatusBadge,
  ToneBadge,
} from '@/components/console/ui';
import { Markdown } from '@/components/console/markdown';
import { RunArtifacts } from '@/components/console/run-artifacts';
import { RunEvents } from '@/components/runs/run-events';
import { useRunEvents } from '@/components/runs/use-run-events';
import {
  RUN_STATUS_LABELS,
  isRunQuiescent,
  relativeTime,
} from '@/lib/presentation';
import type { Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

const CONSOLE_TEXTAREA =
  'w-full resize-y rounded-md border border-ha-border-strong bg-ha-panel-2 px-3 py-2 text-[12px] leading-5 text-ha-text-body outline-none placeholder:text-ha-dim focus:border-ha-accent';

export function RunDetail({
  runId,
  onSelectRun,
  onClose,
}: {
  runId: string;
  onSelectRun?: (nextRunId: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [planDraft, setPlanDraft] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('先别发,我要改一下');
  const [instruction, setInstruction] = useState('');
  const [showArtifact, setShowArtifact] = useState(false);

  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => runsApi.get(runId),
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && isRunQuiescent(data.status) ? false : 2000;
    },
  });
  const planQuery = useQuery({
    queryKey: ['plan', runId],
    queryFn: () => runsApi.getPlan(runId),
  });
  const instructionsQuery = useQuery({
    queryKey: ['instructions', runId],
    queryFn: () => runsApi.instructions(runId),
  });
  const { events, connected } = useRunEvents(runId);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['run', runId] }),
      queryClient.invalidateQueries({ queryKey: ['plan', runId] }),
      queryClient.invalidateQueries({ queryKey: ['instructions', runId] }),
      queryClient.invalidateQueries({ queryKey: ['runs'] }),
    ]);
  };

  const retryMutation = useMutation({
    mutationFn: () => runsApi.retry(runId),
    onSuccess: async (created) => {
      await refresh();
      onSelectRun?.(created.id);
    },
  });
  const action = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: refresh,
  });
  const savePlan = useMutation({
    mutationFn: () => runsApi.updatePlan(runId, planDraft ?? ''),
    onSuccess: refresh,
  });
  const queueInstruction = useMutation({
    mutationFn: () => runsApi.queueInstruction(runId, instruction.trim()),
    onSuccess: async () => {
      setInstruction('');
      await refresh();
    },
  });

  const run = runQuery.data;
  const plan = planQuery.data;
  const status = run?.status ?? 'pending';
  const editable = status === 'awaiting_approval';

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
          <div className="flex flex-wrap items-center gap-2">
            <RunStatusBadge status={status} />
            <span
              className="font-mono text-[11px] text-ha-dim"
              data-testid="run-status"
            >
              {RUN_STATUS_LABELS[status]}
              {run && run.rounds > 0 ? ' · 第 ' + run.rounds + ' 轮' : ''}
            </span>
            <span className="ml-auto font-mono text-[11px] text-ha-faint">
              {relativeTime(run?.created_at)}
            </span>
          </div>
          <p className="mt-2 text-[12px] leading-5 text-ha-muted">
            {run?.goal}
          </p>
          {run?.status_reason ? (
            <p className="mt-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2 text-[12px] leading-5 text-ha-text-2">
              {status === 'failed' ? '失败原因' : '中断原因'}:
              {run.status_reason}
            </p>
          ) : null}
          {run?.parent_run_id ? (
            <p className="mt-2 font-mono text-[11px] text-ha-dim">
              由 run {run.parent_run_id.slice(0, 8)}… 重试而来(来源 run
              的历史与失败原因原样保留)
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            {status === 'pending' ? (
              <ConsoleButton
                variant="primary"
                size="sm"
                onClick={() => action.mutate(() => runsApi.generatePlan(runId))}
                disabled={action.isPending}
              >
                生成计划
              </ConsoleButton>
            ) : null}

            {editable ? (
              <>
                <ConsoleButton
                  variant="primary"
                  size="sm"
                  onClick={() => action.mutate(() => runsApi.approve(runId))}
                  disabled={action.isPending}
                >
                  放行执行
                </ConsoleButton>
                <ConsoleButton
                  variant="danger"
                  size="sm"
                  onClick={() =>
                    action.mutate(() => runsApi.reject(runId, rejectReason))
                  }
                  disabled={action.isPending}
                >
                  拒绝计划
                </ConsoleButton>
                <input
                  aria-label="拒绝原因"
                  value={rejectReason}
                  onChange={(event) => setRejectReason(event.target.value)}
                  className="h-7 min-w-40 flex-1 rounded-md border border-ha-border-strong bg-ha-panel-2 px-2.5 text-[12px] text-ha-text-body outline-none focus:border-ha-accent"
                />
              </>
            ) : null}

            {status === 'blocked' ? (
              <ConsoleButton
                variant="primary"
                size="sm"
                onClick={() => action.mutate(() => runsApi.resume(runId))}
                disabled={action.isPending}
              >
                重新规划并进闸
              </ConsoleButton>
            ) : null}

            {status === 'awaiting_confirmation' ? (
              <>
                <ConsoleButton
                  variant="primary"
                  size="sm"
                  onClick={() =>
                    action.mutate(() => runsApi.confirm(runId, 'approve'))
                  }
                  disabled={action.isPending}
                >
                  确认执行危险操作
                </ConsoleButton>
                <ConsoleButton
                  variant="danger"
                  size="sm"
                  onClick={() =>
                    action.mutate(() => runsApi.confirm(runId, 'reject'))
                  }
                  disabled={action.isPending}
                >
                  拒绝危险操作
                </ConsoleButton>
              </>
            ) : null}

            {status === 'failed' ? (
              <ConsoleButton
                variant="primary"
                size="sm"
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
              >
                {retryMutation.isPending ? '重试中…' : '重试(另起一跑)'}
              </ConsoleButton>
            ) : null}

            {status === 'completed' ? (
              <ConsoleButton
                variant="secondary"
                size="sm"
                onClick={() => setShowArtifact(true)}
              >
                查看交付物
              </ConsoleButton>
            ) : null}
          </div>

          {action.isError ? (
            <p className="mt-2 text-[12px] text-ha-danger">
              {action.error.message}
            </p>
          ) : null}
          {retryMutation.isError ? (
            <p className="mt-2 text-[12px] text-ha-danger">
              {retryMutation.error.message}
            </p>
          ) : null}
        </section>

        <ConsolePanel className="p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-semibold text-ha-text-2">
              计划
            </span>
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
                // 后端只拒绝 completed/failed;blocked 仍可排队(等 resume 时消费)
                status === 'completed' ||
                status === 'failed'
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
                <ToneBadge
                  tone={item.status === 'queued' ? 'warning' : 'success'}
                >
                  {item.status === 'queued' ? '待消费' : '已消费'}
                </ToneBadge>
              </li>
            ))}
          </ul>
        </ConsolePanel>

        {showArtifact ? <RunArtifacts runId={runId} /> : null}

        <ConsolePanel className="p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12px] font-semibold text-ha-text-2">
              事件流
            </span>
            <span
              className={cn(
                'inline-flex items-center gap-1.5 font-mono text-[11px]',
                connected ? 'text-ha-success' : 'text-ha-dim',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  connected ? 'animate-pulse bg-ha-success' : 'bg-ha-ghost',
                )}
              />
              {connected ? 'SSE 已连接' : '已断开'}
            </span>
          </div>
          <div className="mt-2 max-h-72 overflow-y-auto">
            <RunEvents events={events} />
          </div>
        </ConsolePanel>
      </div>
    </aside>
  );
}
