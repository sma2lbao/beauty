import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

import { AuthBar } from '@/components/runs/auth-bar';
import { useRunEvents } from '@/components/runs/use-run-events';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { titleFromMessage } from '@/lib/chat';
import type { Run, RunStatus } from '@/lib/runs-api';
import { isUnauthorized, runsApi } from '@/lib/runs-api';
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

/** 终态:不会再自己变化(blocked 需人工 resume)。 */
const TERMINAL: readonly RunStatus[] = ['completed', 'failed', 'blocked'];

function isTerminal(status: RunStatus): boolean {
  return TERMINAL.includes(status);
}

type TranscriptItem = {
  key: string;
  role: 'agent';
  text: string;
  tone?: 'info' | 'warn' | 'error';
};

function asText(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value ? value : fallback;
}

/**
 * 把 run 事件翻成对话里的一行行"agent 说话"。
 *
 * 事件是审计流水(§6.3),这里只做展示映射——不改变任何状态语义。
 */
function transcriptFrom(events: { seq: number; type: string; payload: Record<string, unknown> }[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  for (const event of events) {
    const payload = event.payload ?? {};
    const push = (text: string, tone?: TranscriptItem['tone']) =>
      items.push({ key: `${event.seq}`, role: 'agent', text, tone });
    switch (event.type) {
      case 'run.created.v1':
        push(`已建立任务《${asText(payload.title, '未命名')}》,开始规划…`);
        break;
      case 'run.planned.v1':
        push(`计划已就绪(第 ${payload.attempt ?? 1} 次尝试),等你确认后执行。`);
        break;
      case 'run.approved.v1':
        push('已放行,开始执行。');
        break;
      case 'run.tool.called.v1':
        push(
          `调用工具 ${asText(payload.name, '未知')} —${payload.ok === false ? '失败' : '成功'}`,
          payload.ok === false ? 'warn' : 'info',
        );
        break;
      case 'run.confirmation.required.v1':
        push('有写操作需要你确认(例如发信),确认后才会执行。', 'warn');
        break;
      case 'run.confirmation.received.v1':
        push(`写操作确认:${asText(payload.action, '已回复')}`);
        break;
      case 'run.instruction.queued.v1':
        push('已记下你的补充,会在下一个边界并入执行。');
        break;
      case 'run.instruction.consumed.v1':
        push('补充指令已并入本次执行。');
        break;
      case 'run.rejected.v1':
        push(`计划被拒绝:${asText(payload.reason, '未填原因')}`, 'warn');
        break;
      case 'run.resumed.v1':
        push('已重新进入审批。');
        break;
      case 'run.retry.requested.v1':
        push('已另起一跑重试(原任务历史保留)。');
        break;
      case 'run.failed.v1':
        push(`执行失败(${asText(payload.stage, '未知阶段')}):${asText(payload.reason, '未知原因')}`, 'error');
        break;
      case 'run.blocked.v1':
        push(`任务中断:${asText(payload.reason, '需要人工处理')}`, 'warn');
        break;
      case 'run.delivered.v1':
        push(`已交付《${asText(payload.title, '成品')}》。`);
        break;
      case 'run.completed.v1':
        push('任务完成 ✅');
        break;
      default:
        break;
    }
  }
  return items;
}

/**
 * 对话页(首页):用说话的方式派活给 Agent。
 *
 * - 没有进行中的任务时,发送 = 新建任务;有进行中的任务时,发送 = 追加指令(§6.5);
 * - Agent 的回复来自该 run 的事件流(SSE),所以"说了什么"和审计流水一致;
 * - 需要人工决策的地方(放行计划/确认写操作)就地给按钮,深链路(重试/恢复/审计)
 *   留到运行控制台。
 */
export function ChatPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [localMessages, setLocalMessages] = useState<Record<string, string[]>>({});
  const [rejectReason, setRejectReason] = useState('先别发,我要改一下');
  const [showArtifact, setShowArtifact] = useState(false);

  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: runsApi.list });
  const runs = runsQuery.data ?? [];

  const runQuery = useQuery({
    queryKey: ['run', activeRunId],
    queryFn: () => runsApi.get(activeRunId as string),
    enabled: Boolean(activeRunId),
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && isTerminal(data.status) ? false : 2000;
    },
  });
  const { events, connected } = useRunEvents(activeRunId);
  const artifactQuery = useQuery({
    queryKey: ['artifact', activeRunId],
    queryFn: () => runsApi.artifact(activeRunId as string),
    enabled: Boolean(activeRunId) && showArtifact,
    retry: false,
  });

  const run = runQuery.data;
  const status: RunStatus = run?.status ?? 'pending';
  const transcript = transcriptFrom(events);

  const sendMessage = useMutation({
    mutationFn: (text: string) =>
      runsApi.create({
        title: titleFromMessage(text),
        goal: text,
        // 对话页不强制填验收标准:留空表示由 Agent 按目标自行判断
        acceptance_criteria: [],
      }),
    onSuccess: (created, text) => {
      setLocalMessages((previous) => ({ ...previous, [created.id]: [text] }));
      setShowArtifact(false);
      setActiveRunId(created.id);
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
    },
  });

  const sendInstruction = useMutation({
    mutationFn: (text: string) =>
      runsApi.queueInstruction(activeRunId as string, text),
    onSuccess: (_instruction, text) => {
      setLocalMessages((previous) => ({
        ...previous,
        [activeRunId as string]: [...(previous[activeRunId as string] ?? []), text],
      }));
    },
  });

  const action = useMutation({
    mutationFn: (task: () => Promise<unknown>) => task(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', activeRunId] }),
        queryClient.invalidateQueries({ queryKey: ['runs'] }),
      ]);
    },
  });

  const busy = sendMessage.isPending || sendInstruction.isPending;
  const error =
    sendMessage.error ??
    sendInstruction.error ??
    action.error ??
    runQuery.error ??
    runsQuery.error;
  const unauthorized = isUnauthorized(error);
  const canSend = draft.trim().length > 0 && !busy;
  /** 有进行中的任务时,发送变成"追加指令"(不新开一跑)。 */
  const inFlight = Boolean(activeRunId) && run !== undefined && !isTerminal(status);

  const submit = () => {
    const text = draft.trim();
    if (!text) {
      return;
    }
    setDraft('');
    if (inFlight) {
      sendInstruction.mutate(text);
    } else {
      sendMessage.mutate(text);
    }
  };

  const messages = activeRunId ? localMessages[activeRunId] ?? [] : [];

  const newConversation = () => {
    setActiveRunId(null);
    setDraft('');
    setShowArtifact(false);
  };

  return (
    <section className="mx-auto max-w-6xl px-6 py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl">对话</h1>
          <p className="mt-2 max-w-2xl leading-relaxed text-muted-foreground">
            直接说要做什么:Agent 会先给计划、等你放行再执行,写操作(比如发信)会单独确认。
            {activeRunId ? (
              <span className="ml-1 text-xs">
                {connected ? '· 事件流已连接' : '· 事件流未连接'}
              </span>
            ) : null}
          </p>
        </div>
        <Button variant="outline" onClick={newConversation} disabled={!activeRunId}>
          新对话
        </Button>
      </div>

      <div className="mt-6">
        <AuthBar onChange={() => void runsQuery.refetch()} />
      </div>

      {unauthorized ? (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
          未登录或令牌失效:先在上方面板完成鉴权,再开始对话。
        </p>
      ) : null}

      <div className="mt-6 grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card className="flex min-h-[28rem] flex-col">
          <CardHeader>
            <CardTitle>
              {run ? `${run.title} · ${STATUS_LABELS[status]}` : '还没开始的任务'}
            </CardTitle>
            <CardDescription>
              {run
                ? `目标:${run.goal}${run.rounds > 0 ? ` · 第 ${run.rounds} 轮` : ''}`
                : '在下面说说你要做什么,例如「把本周邮件整理成周报」'}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-4">
            <div className="flex-1 space-y-3">
              {!activeRunId ? (
                <p className="text-sm text-muted-foreground">
                  对话从这里开始。每条消息会变成一次任务的 goal,
                  Agent 的行动会实时出现在这里。
                </p>
              ) : null}

              {messages.map((text, index) => (
                <div key={`user-${index}`} className="flex justify-end">
                  <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
                    {text}
                  </p>
                </div>
              ))}

              {transcript.map((item) => (
                <div key={item.key} className="flex justify-start">
                  <p
                    className={cn(
                      'max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm border px-3 py-2 text-sm',
                      item.tone === 'error'
                        ? 'border-destructive/40 bg-destructive/10 text-destructive'
                        : item.tone === 'warn'
                          ? 'border-amber-500/40 bg-amber-500/10'
                          : 'bg-muted/50',
                    )}
                  >
                    {item.text}
                  </p>
                </div>
              ))}

              {run?.status_reason ? (
                <p className="text-sm text-muted-foreground">
                  {`${status === 'failed' ? '失败原因' : '中断原因'}:${run.status_reason}`}
                </p>
              ) : null}

              {run?.status === 'awaiting_approval' ? (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed p-3">
                  <Button
                    size="sm"
                    onClick={() => action.mutate(() => runsApi.approve(run.id))}
                    disabled={action.isPending}
                  >
                    放行执行
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      action.mutate(() => runsApi.reject(run.id, rejectReason))
                    }
                    disabled={action.isPending}
                  >
                    拒绝计划
                  </Button>
                  <input
                    aria-label="拒绝原因"
                    className="min-w-40 flex-1 rounded-md border bg-transparent px-3 py-1.5 text-sm"
                    value={rejectReason}
                    onChange={(event) => setRejectReason(event.target.value)}
                  />
                </div>
              ) : null}

              {run?.status === 'awaiting_confirmation' ? (
                <div className="flex flex-wrap gap-2 rounded-lg border border-dashed p-3">
                  <Button
                    size="sm"
                    onClick={() => action.mutate(() => runsApi.confirm(run.id, 'approve'))}
                    disabled={action.isPending}
                  >
                    确认执行写操作
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => action.mutate(() => runsApi.confirm(run.id, 'reject'))}
                    disabled={action.isPending}
                  >
                    拒绝写操作
                  </Button>
                </div>
              ) : null}

              {status === 'completed' ? (
                <div className="space-y-3">
                  <Button size="sm" variant="outline" onClick={() => setShowArtifact(true)}>
                    查看成品
                  </Button>
                  {showArtifact ? (
                    artifactQuery.isLoading ? (
                      <p className="text-sm text-muted-foreground">读取中…</p>
                    ) : artifactQuery.data ? (
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-sm">
                        {artifactQuery.data.content}
                      </pre>
                    ) : null
                  ) : null}
                </div>
              ) : null}

              {isTerminal(status) && activeRunId ? (
                <p className="text-xs text-muted-foreground">
                  需要重试、恢复或看完整审计流水?去
                  <Link className="mx-1 underline" to="/runs">
                    运行控制台
                  </Link>
                  。
                </p>
              ) : null}
            </div>

            <form
              className="flex flex-wrap items-end gap-2 border-t pt-4"
              onSubmit={(event) => {
                event.preventDefault();
                submit();
              }}
            >
              <label className="sr-only" htmlFor="chat-input">
                说点什么
              </label>
              <textarea
                id="chat-input"
                className="min-h-16 flex-1 rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring"
                placeholder={
                  inFlight
                    ? '补充一句(会在下一个边界并入执行)…'
                    : '例如:把本周邮件整理成周报'
                }
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
              />
              <Button type="submit" disabled={!canSend}>
                {inFlight ? '补充指令' : '发送'}
              </Button>
            </form>
            <p className="text-xs text-muted-foreground">
              {inFlight
                ? '当前任务还没结束:这条会作为追加指令并入本次执行(想开新任务先点「新对话」)。'
                : 'Enter 发送,Shift+Enter 换行。'}
            </p>
            {error && !unauthorized ? (
              <p className="text-sm text-destructive">
                {error instanceof Error ? error.message : '发送失败'}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>最近任务</CardTitle>
            <CardDescription>点一条切回那段对话</CardDescription>
          </CardHeader>
          <CardContent>
            {runsQuery.isLoading ? (
              <p className="text-sm text-muted-foreground">加载中…</p>
            ) : runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">还没有任务。</p>
            ) : (
              <ul className="space-y-2">
                {runs.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setActiveRunId(item.id);
                        setShowArtifact(false);
                      }}
                      className={cn(
                        'w-full rounded-md border px-3 py-2 text-left text-sm transition-colors',
                        item.id === activeRunId ? 'border-primary bg-muted' : 'hover:bg-muted/60',
                      )}
                    >
                      <span className="font-medium">{item.title}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {STATUS_LABELS[item.status]} ·{' '}
                        {new Date(item.created_at).toLocaleString('zh-CN')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
