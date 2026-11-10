/**
 * 新建任务弹窗:标题/目标/验收标准(每行一条),创建成功后选中新 run。
 */
import { Dialog, Field, Input, Textarea } from '@beauty/design';

import { ConsoleButton } from '@/components/console/ui';

import type { RunsModel } from './use-runs-model';

export function RunCreateDialog({ model }: { model: RunsModel }) {
  const {
    createError,
    createMutation,
    createOpen,
    criteria,
    goal,
    priority,
    setCreateOpen,
    setCriteria,
    setGoal,
    setPriority,
    setTimeoutSeconds,
    setTitle,
    submitCreate,
    timeoutSeconds,
    title,
  } = model;
  return (
    <Dialog
      open={createOpen}
      onOpenChange={setCreateOpen}
      title="新建任务"
      description="验收标准每行一条。创建后先规划,等你放行再执行。"
      footer={
        <>
          <ConsoleButton
            variant="secondary"
            size="md"
            onClick={() => setCreateOpen(false)}
          >
            取消
          </ConsoleButton>
          <ConsoleButton
            variant="primary"
            size="md"
            onClick={submitCreate}
            disabled={createMutation.isPending || !title.trim() || !goal.trim()}
          >
            {createMutation.isPending ? '创建中…' : '创建'}
          </ConsoleButton>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="标题" htmlFor="new-run-title">
          <Input
            id="new-run-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label="目标" htmlFor="new-run-goal">
          <Textarea
            id="new-run-goal"
            rows={3}
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
          />
        </Field>
        <Field label="验收标准" htmlFor="new-run-criteria">
          <Textarea
            id="new-run-criteria"
            rows={3}
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="超时(秒)"
            htmlFor="new-run-timeout"
            hint="留空 = 平台默认,0 = 不限,最大 604800。"
          >
            <Input
              id="new-run-timeout"
              type="number"
              min={0}
              step={1}
              placeholder="平台默认"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
            />
          </Field>
          <Field
            label="优先级"
            htmlFor="new-run-priority"
            hint="-100..100,越大越先调度。"
          >
            <Input
              id="new-run-priority"
              type="number"
              min={-100}
              max={100}
              step={1}
              value={priority}
              onChange={(event) => setPriority(event.target.value)}
            />
          </Field>
        </div>
        {createError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            {createError}
          </p>
        ) : null}
        {createMutation.isError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            {createMutation.error.message}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
