/**
 * 运行详情「检查点」页签(P1-C):列出图检查点,并就地发起回退/分叉。
 *
 * 回退只对 runnable 且 run 处于终态的检查点开放(后端状态机要求);
 * 两件写操作都走二次确认弹窗,原因/标题可选,错误就地中文提示。
 */
import { Checkbox, Field, Input } from '@beauty/design';
import { GitBranch, RefreshCw, RotateCcw } from 'lucide-react';

import { ConsoleDialog } from '@/components/console/dialog';
import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  Mono,
  ToneBadge,
} from '@/components/console/ui';
import { relativeTime } from '@/lib/presentation';
import { summarizeArguments } from '@/lib/presentation/tools';
import type { Run, RunCheckpoint, RunSideEffect } from '@/lib/runs-api';

import {
  REWINDABLE_RUN_STATUSES,
  checkpointErrorMessage,
  useRunCheckpoints,
} from './use-run-checkpoints';

export function RunDetailCheckpointsPanel({
  runId,
  run,
  onSelectRun,
}: {
  runId: string;
  run: Run | undefined;
  onSelectRun?: (nextRunId: string) => void;
}) {
  const model = useRunCheckpoints({ runId, onSelectRun });
  const statusRewindable = run ? REWINDABLE_RUN_STATUSES.includes(run.status) : false;

  return (
    <>
      <ConsolePanel className="p-3" data-testid="run-checkpoints-panel">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <span className="text-[12px] font-semibold text-ha-text-2">
              检查点
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              {model.checkpoints.length} 个
            </span>
          </span>
          <ConsoleButton
            size="sm"
            variant="secondary"
            aria-label="刷新检查点"
            onClick={model.refetch}
          >
            <RefreshCw aria-hidden className="size-3.5" />
            刷新
          </ConsoleButton>
        </div>

        {model.error ? (
          <div
            role="alert"
            className="mt-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-2.5 py-2"
          >
            <p className="text-[12px] leading-5 text-ha-danger">
              读取检查点失败:{checkpointErrorMessage(model.error, '请稍后重试。')}
            </p>
            <ConsoleButton size="sm" variant="secondary" className="mt-1.5" onClick={model.refetch}>
              重试
            </ConsoleButton>
          </div>
        ) : model.isLoading ? (
          <p className="mt-3 text-[12px] text-ha-muted">加载检查点…</p>
        ) : model.checkpoints.length === 0 ? (
          <EmptyPanel
            className="mt-3"
            title="还没有检查点"
            description="这个 run 尚未执行过图节点;产生检查点后才能回退或分叉。"
          />
        ) : (
          <ul className="mt-3 divide-y divide-ha-border">
            {model.checkpoints.map((checkpoint) => (
              <CheckpointRow
                key={checkpoint.checkpoint_id}
                checkpoint={checkpoint}
                statusRewindable={statusRewindable}
                onRewind={() => model.openRewind(checkpoint)}
                onFork={() => model.openFork(checkpoint)}
              />
            ))}
          </ul>
        )}
      </ConsolePanel>

      <ConsoleDialog
        open={model.rewindTarget !== null}
        title="回退到此检查点?"
        description="后面的节点会按未执行重新排队,run 仍是同一个;已产生的消息/文件等外部副作用不会回滚。"
        onClose={model.closeRewind}
        footer={
          <>
            <ConsoleButton variant="secondary" size="md" onClick={model.closeRewind}>
              取消
            </ConsoleButton>
            <ConsoleButton
              variant="primary"
              size="md"
              disabled={model.rewindPending || model.rewindBlocked}
              title={
                model.rewindBlocked ? '请先勾选「我已知悉」' : undefined
              }
              onClick={model.confirmRewind}
            >
              {model.rewindPending ? '回退中…' : '确认回退'}
            </ConsoleButton>
          </>
        }
      >
        <div className="space-y-3">
          <p className="font-mono text-[11px] text-ha-dim">
            目标检查点:{model.rewindTarget?.checkpoint_id}
          </p>
          <SideEffectNotice
            effects={model.sideEffects}
            loading={model.sideEffectsLoading}
            error={model.sideEffectsError}
            acknowledged={model.acknowledgeSideEffects}
            onAcknowledgeChange={model.setAcknowledgeSideEffects}
          />
          <Field label="回退原因(可选)" htmlFor="run-rewind-reason">
            <Input
              id="run-rewind-reason"
              value={model.reason}
              placeholder="例如:计划里第 2 步的判断错了"
              onChange={(event) => model.setReason(event.target.value)}
            />
          </Field>
          {model.rewindError ? (
            <p role="alert" className="text-[12px] text-ha-danger">
              {checkpointErrorMessage(model.rewindError)}
            </p>
          ) : null}
        </div>
      </ConsoleDialog>

      <ConsoleDialog
        open={model.forkTarget !== null}
        title="从检查点分叉"
        description="会新建一个 run 从该检查点继续执行,当前 run 保持不变;新 run 继承原目标、计划与超时/优先级。"
        onClose={model.closeFork}
        footer={
          <>
            <ConsoleButton variant="secondary" size="md" onClick={model.closeFork}>
              取消
            </ConsoleButton>
            <ConsoleButton
              variant="primary"
              size="md"
              disabled={model.forkPending || model.forkBlocked}
              title={model.forkBlocked ? '请先勾选「我已知悉」' : undefined}
              onClick={model.confirmFork}
            >
              {model.forkPending ? '分叉中…' : '确认分叉'}
            </ConsoleButton>
          </>
        }
      >
        <div className="space-y-3">
          <p className="font-mono text-[11px] text-ha-dim">
            源检查点:{model.forkTarget?.checkpoint_id}
          </p>
          <SideEffectNotice
            effects={model.sideEffects}
            loading={model.sideEffectsLoading}
            error={model.sideEffectsError}
            acknowledged={model.acknowledgeSideEffects}
            onAcknowledgeChange={model.setAcknowledgeSideEffects}
          />
          <Field label="新 run 标题(可选)" htmlFor="run-fork-title">
            <Input
              id="run-fork-title"
              value={model.forkTitle}
              placeholder={run ? run.title + '(分叉)' : '默认沿用父 run 标题(分叉)'}
              onChange={(event) => model.setForkTitle(event.target.value)}
            />
          </Field>
          {model.forkError ? (
            <p role="alert" className="text-[12px] text-ha-danger">
              {checkpointErrorMessage(model.forkError)}
            </p>
          ) : null}
        </div>
      </ConsoleDialog>
    </>
  );
}

/** 单条检查点:元信息 + 摘要 + 两个操作;回退按钮不可用时用 title 解释原因。 */
function CheckpointRow({
  checkpoint,
  statusRewindable,
  onRewind,
  onFork,
}: {
  checkpoint: RunCheckpoint;
  statusRewindable: boolean;
  onRewind: () => void;
  onFork: () => void;
}) {
  const canRewind = checkpoint.runnable && statusRewindable;
  const rewindHint = !checkpoint.runnable
    ? '该检查点之后已无待跑节点,无需回退'
    : !statusRewindable
      ? '运行中不能回退,请先取消任务'
      : undefined;
  const summary = checkpoint.summary;
  return (
    <li
      data-testid={'run-checkpoint-' + checkpoint.checkpoint_id}
      className="py-3 first:pt-0 last:pb-0"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-ha-text-2">
          {checkpoint.step === null ? '—' : '#' + checkpoint.step}
        </span>
        <Mono>{relativeTime(checkpoint.created_at)}</Mono>
        {checkpoint.source ? <Mono>source {checkpoint.source}</Mono> : null}
        {checkpoint.runnable ? (
          <ToneBadge tone="success">可回退</ToneBadge>
        ) : (
          <ToneBadge tone="neutral">已推进</ToneBadge>
        )}
      </div>
      <p className="mt-1.5 truncate font-mono text-[11px] text-ha-dim" title={checkpoint.checkpoint_id}>
        id {checkpoint.checkpoint_id}
      </p>
      <p className="mt-1 truncate font-mono text-[11px] text-ha-faint">
        next: {checkpoint.next.length > 0 ? checkpoint.next.join(' → ') : '—'}
      </p>
      <p className="mt-1 font-mono text-[11px] text-ha-faint">
        轮次 {summary.rounds} · 计划 {summary.plan_chars} 字 · 工具{' '}
        {summary.tool_calls} 次 · 待办 {summary.todos_count} · 产物{' '}
        {summary.artifact_ready ? '已就绪' : '无'}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <ConsoleButton
          size="sm"
          variant="primary"
          disabled={!canRewind}
          title={rewindHint}
          onClick={onRewind}
        >
          <RotateCcw aria-hidden className="size-3.5" />
          回退到此
        </ConsoleButton>
        <ConsoleButton size="sm" variant="secondary" onClick={onFork}>
          <GitBranch aria-hidden className="size-3.5" />
          分叉
        </ConsoleButton>
      </div>
    </li>
  );
}

/**
 * 回退/分叉前的副作用台账(S5)。
 *
 * 后端只在 run 有外部写时要求确认,这里先列清楚「写过什么」再让人勾选:
 * 回退不会撤销写,同参调用按稳定幂等键重放、异参就是新的写。
 * 台账读不到时不假装没写——提示读取失败,提交期仍由后端 409 拦截。
 */
function SideEffectNotice({
  effects,
  loading,
  error,
  acknowledged,
  onAcknowledgeChange,
}: {
  effects: RunSideEffect[];
  loading: boolean;
  error: unknown;
  acknowledged: boolean;
  onAcknowledgeChange: (next: boolean) => void;
}) {
  if (loading) {
    return <p className="text-[12px] text-ha-muted">正在检查外部副作用…</p>;
  }
  if (error) {
    return (
      <p role="alert" className="text-[12px] leading-5 text-ha-warning">
        外部副作用台账读取失败:
        {checkpointErrorMessage(error, '提交时仍会拦截未确认的写操作。')}
      </p>
    );
  }
  if (effects.length === 0) {
    return null;
  }
  return (
    <div
      data-testid="run-side-effects-notice"
      className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-2.5 py-2"
    >
      <p className="text-[12px] font-semibold text-ha-warning">
        这个 run 已经发起 {effects.length} 次外部写
      </p>
      <p className="mt-1 text-[12px] leading-5 text-ha-warning">
        回退/分叉重跑会再次执行这些节点:同参数调用按幂等键重放,参数变了就是一次新的写;
        已发生的写不会回滚。
      </p>
      <ul className="mt-2 space-y-1">
        {effects.map((effect) => (
          <li
            key={effect.seq + '-' + effect.name}
            className="flex flex-wrap items-center gap-1.5"
          >
            <ToneBadge tone={effect.risk === 'danger' ? 'danger' : 'warning'}>
              {effect.risk === 'danger' ? 'DANGER' : 'WRITE'}
            </ToneBadge>
            <Mono>{effect.name}</Mono>
            <Mono>{effect.ok ? '已执行' : '未成功'}</Mono>
            {effect.arguments ? (
              <span className="font-mono text-[11px] text-ha-faint">
                {summarizeArguments(effect.arguments)}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="mt-2">
        <Checkbox
          checked={acknowledged}
          onCheckedChange={onAcknowledgeChange}
          label="我已知悉,仍要继续(写操作可能重复执行)"
        />
      </div>
    </div>
  );
}
