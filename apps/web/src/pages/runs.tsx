import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { RunEvents } from '@/components/runs/run-events';
import { useRunEvents } from '@/components/runs/use-run-events';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import type { Run, RunStatus } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

const STATUS_LABELS: Record<RunStatus, string> = {
  pending: '待规划',
  planning: '规划中',
  awaiting_approval: '待审批',
  running: '执行中',
  awaiting_confirmation: '待确认写操作',
  blocked: '已阻塞',
  completed: '已完成',
  failed: '已失败',
};

const TERMINAL: readonly RunStatus[] = ['completed', 'failed'];

function isTerminal(status: RunStatus): boolean {
  return TERMINAL.includes(status);
}

const FIELD_CLASS =
  'mt-1 w-full rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring';

export function RunsPage() {
  const queryClient = useQueryClient();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [title, setTitle] = useState('整理本周邮件成周报');
  const [goal, setGoal] = useState('汇总本周邮件,生成周报草稿');
  const [criteria, setCriteria] = useState('覆盖 5 个工作日\n不超过 300 字');

  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: runsApi.list });
  const createMutation = useMutation({
    mutationFn: () =>
      runsApi.create({
        title: title.trim(),
        goal: goal.trim(),
        acceptance_criteria: criteria
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean),
      }),
    onSuccess: async (run) => {
      setSelectedRunId(run.id);
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
    },
  });

  const runs = runsQuery.data ?? [];

  return (
    <section className="mx-auto max-w-6xl px-6 py-12">
      <h1 className="font-display text-3xl">运行控制台</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        建 run → 审批计划 → 看 Agent 调工具 → 确认写操作 → 取交付物。
        事件流走 SSE,实时推送。
      </p>

      <div className="mt-10 grid gap-6 lg:grid-cols-[minmax(280px,340px)_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>新建 run</CardTitle>
              <CardDescription>验收标准每行一条。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <label className="block text-sm">
                标题
                <input
                  className={FIELD_CLASS}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
              <label className="block text-sm">
                目标
                <textarea
                  className={FIELD_CLASS}
                  rows={3}
                  value={goal}
                  onChange={(event) => setGoal(event.target.value)}
                />
              </label>
              <label className="block text-sm">
                验收标准
                <textarea
                  className={FIELD_CLASS}
                  rows={3}
                  value={criteria}
                  onChange={(event) => setCriteria(event.target.value)}
                />
              </label>
              <Button
                onClick={() => createMutation.mutate()}
                disabled={
                  createMutation.isPending || !title.trim() || !goal.trim()
                }
              >
                {createMutation.isPending ? '创建中…' : '创建'}
              </Button>
              {createMutation.isError ? (
                <p className="text-sm text-destructive">
                  {createMutation.error.message}
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>运行列表</CardTitle>
              <CardDescription>最新在前,点选查看详情。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {runsQuery.isPending ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : null}
              {runsQuery.isError ? (
                <p className="text-sm text-destructive">
                  列表加载失败:{runsQuery.error.message}
                </p>
              ) : null}
              {!runsQuery.isPending && runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">还没有 run。</p>
              ) : null}
              {runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  aria-pressed={run.id === selectedRunId}
                  onClick={() => setSelectedRunId(run.id)}
                  className={cn(
                    'w-full rounded-lg border p-3 text-left text-sm transition-colors',
                    run.id === selectedRunId
                      ? 'border-primary'
                      : 'hover:border-primary/60',
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">{run.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {STATUS_LABELS[run.status]}
                    </span>
                  </span>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">
                    {run.goal}
                  </span>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>

        <div>
          {selectedRunId ? (
            <RunDetail runId={selectedRunId} />
          ) : (
            <div className="rounded-xl border border-dashed p-10 text-sm text-muted-foreground">
              左侧选一个 run,或先新建一个。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function RunDetail({ runId }: { runId: string }) {
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
      return data && isTerminal(data.status) ? false : 2000;
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
  const artifactQuery = useQuery({
    queryKey: ['artifact', runId],
    queryFn: () => runsApi.artifact(runId),
    enabled: showArtifact,
    retry: false,
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
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <CardTitle>{run ? run.title : '加载中…'}</CardTitle>
            <span className="text-sm text-muted-foreground" data-testid="run-status">
              {STATUS_LABELS[status]}
              {run && run.rounds > 0 ? ` · 第 ${run.rounds} 轮` : ''}
            </span>
          </div>
          <CardDescription>{run?.goal}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {run?.blocked_reason ? (
            <p className="rounded-lg border border-dashed p-3 text-sm">
              中断原因:{run.blocked_reason}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {status === 'pending' ? (
              <Button
                onClick={() => action.mutate(() => runsApi.generatePlan(runId))}
                disabled={action.isPending}
              >
                生成计划
              </Button>
            ) : null}

            {editable ? (
              <>
                <Button
                  onClick={() => action.mutate(() => runsApi.approve(runId))}
                  disabled={action.isPending}
                >
                  放行执行
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    action.mutate(() => runsApi.reject(runId, rejectReason))
                  }
                  disabled={action.isPending}
                >
                  拒绝计划
                </Button>
                <input
                  aria-label="拒绝原因"
                  className="rounded-md border bg-transparent px-3 py-2 text-sm"
                  value={rejectReason}
                  onChange={(event) => setRejectReason(event.target.value)}
                />
              </>
            ) : null}

            {status === 'awaiting_confirmation' ? (
              <>
                <Button
                  onClick={() =>
                    action.mutate(() => runsApi.confirm(runId, 'approve'))
                  }
                  disabled={action.isPending}
                >
                  确认执行写操作
                </Button>
                <Button
                  variant="outline"
                  onClick={() => action.mutate(() => runsApi.confirm(runId, 'reject'))}
                  disabled={action.isPending}
                >
                  拒绝写操作
                </Button>
              </>
            ) : null}

            {status === 'completed' ? (
              <Button variant="outline" onClick={() => setShowArtifact(true)}>
                查看交付物
              </Button>
            ) : null}
          </div>

          {action.isError ? (
            <p className="text-sm text-destructive">{action.error.message}</p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>计划</CardTitle>
          <CardDescription>
            {editable ? '审批前可编辑,保存后生效。' : '生成后显示,审批前可编辑。'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            aria-label="计划内容"
            className={cn(FIELD_CLASS, 'font-mono text-xs')}
            rows={12}
            readOnly={!editable}
            value={planDraft ?? plan?.plan_markdown ?? ''}
            onChange={(event) => setPlanDraft(event.target.value)}
          />
          {editable ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => savePlan.mutate()}
              disabled={savePlan.isPending}
            >
              保存计划
            </Button>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>追加指令(§6.5)</CardTitle>
          <CardDescription>
            排队后在下一个人工边界(规划/放行/确认)并入执行,模型必须遵循。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            aria-label="追加指令"
            className={FIELD_CLASS}
            rows={2}
            placeholder="例如:顺便把日历也汇总一下"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => queueInstruction.mutate()}
            disabled={
              queueInstruction.isPending ||
              !instruction.trim() ||
              !run ||
              isTerminal(status)
            }
          >
            提交指令
          </Button>
          {queueInstruction.isError ? (
            <p className="text-sm text-destructive">
              {queueInstruction.error.message}
            </p>
          ) : null}
          <ul className="space-y-2 text-sm">
            {(instructionsQuery.data ?? []).map((item) => (
              <li key={item.id} className="rounded-lg border p-3">
                <span className="flex items-center justify-between gap-2">
                  <span>{item.content}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {item.status === 'queued' ? '待消费' : '已消费'}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {showArtifact ? (
        <Card>
          <CardHeader>
            <CardTitle>交付物</CardTitle>
            <CardDescription>
              {artifactQuery.isError
                ? '还没有交付物。'
                : (artifactQuery.data?.title ?? '加载中…')}
            </CardDescription>
          </CardHeader>
          {artifactQuery.data ? (
            <CardContent>
              <pre className="whitespace-pre-wrap text-sm">
                {artifactQuery.data.content}
              </pre>
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div className="flex items-baseline justify-between gap-2">
            <CardTitle>事件流</CardTitle>
            <span className="text-xs text-muted-foreground">
              {connected ? 'SSE 已连接' : '已断开'}
            </span>
          </div>
          <CardDescription>事件溯源读端:seq 单调递增,可回放。</CardDescription>
        </CardHeader>
        <CardContent>
          <RunEvents events={events} />
        </CardContent>
      </Card>
    </div>
  );
}
