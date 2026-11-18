/**
 * run 事件流 → 读模型投影(步骤时间线 / 任务清单),以及事件驱动的失效定位器。
 *
 * 服务端这两个读模型本来就是事件投影:`GET /runs/{id}/steps` 只是把
 * `run.step.v1` 过滤出来(services/agent-orchestrator/agent_orchestrator/services/run_steps.py),
 * 清单变更事件的 payload 也已经是全量 `{todos, counts, revision}`
 * (services/agent-orchestrator/agent_orchestrator/services/todos.py)。
 * SSE 打开 `include_steps` 后这些事件本来就到了前端,再按 1.5s 去拉同一份投影
 * 就是重复请求,所以这里在客户端做同一份投影。事件按 seq 升序增量合并
 * (`mergeEvents`),不排序、不改状态语义。
 *
 * `lastStatusEventSeq` / `lastPlanEventSeq` 给数据层做失效驱动:只有「可能改变
 * run 状态或计划」的事件才算,步骤/过程项/工具调用/正文增量这类高频投影事件不算。
 */
import type {
  RunEvent,
  RunStep,
  RunTodos,
  RunTodoItem,
  StepStatus,
} from '@/lib/runs-api';

import { asText } from './format';

const STEP_EVENT = 'run.step.v1';
const TODO_EVENT = 'run.todo.updated.v1';

/** 高频投影类事件:只改展示细节,不改 run 状态,不该触发 run 重取。 */
const STATUS_NEUTRAL_EVENTS = new Set([
  'run.step.v1',
  'run.item.v1',
  'run.tool.called.v1',
  'run.todo.updated.v1',
  'token.delta.v1',
  'run.usage.v1',
  'run.score.v1',
  'run.context.assembled.v1',
  'run.compacted.v1',
  'run.llm.fallback.v1',
  'run.skill.loaded.v1',
  'run.instruction.queued.v1',
  'run.instruction.consumed.v1',
]);

/** 计划可能变化的事件:规划完成、审批前编辑计划、重规划(新 attempt)。 */
const PLAN_EVENTS = new Set([
  'run.planned.v1',
  'run.plan.edited.v1',
]);

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function asStepStatus(value: unknown): StepStatus {
  return value === 'running' ||
    value === 'failed' ||
    value === 'blocked' ||
    value === 'cancelled'
    ? value
    : 'completed';
}

/**
 * 步骤投影,与后端 `StepRead` 同形:每节点一条 `run.step.v1`,seq 即顺序。
 *
 * 字段一律兜底(空串/0/null):事件里缺字段只该让这一行显示得寡淡,
 * 不该让整个步骤面板炸掉。
 */
export function stepsFrom(events: readonly RunEvent[]): RunStep[] {
  return events
    .filter((event) => event.type === STEP_EVENT)
    .map((event) => ({
      seq: event.seq,
      step_id: asText(event.payload.step_id),
      node: asText(event.payload.node),
      phase: asText(event.payload.phase),
      attempt: asNumber(event.payload.attempt),
      round: asNumber(event.payload.round),
      status: asStepStatus(event.payload.status),
      started_at: asText(event.payload.started_at),
      finished_at: asText(event.payload.finished_at) || null,
      duration_ms:
        typeof event.payload.duration_ms === 'number'
          ? event.payload.duration_ms
          : null,
    }));
}

/**
 * 任务清单投影:取最后一条 `run.todo.updated.v1`(payload 是全量快照)。
 *
 * 没有该事件时返回空清单(revision 0),与后端「从未调用 todo 工具」同形;
 * counts 在客户端重算,不信任 payload 里的聚合值(它是展示用的第二手数据)。
 */
export function todosFrom(events: readonly RunEvent[]): RunTodos {
  let latest: RunEvent | undefined;
  for (const event of events) {
    if (event.type === TODO_EVENT) {
      latest = event;
    }
  }
  if (!latest) {
    return {
      todos: [],
      counts: { pending: 0, in_progress: 0, completed: 0 },
      revision: 0,
    };
  }
  const raw = Array.isArray(latest.payload.todos) ? latest.payload.todos : [];
  const todos: RunTodoItem[] = raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null) {
      return [];
    }
    const entry = item as Record<string, unknown>;
    const status: RunTodoItem['status'] =
      entry.status === 'in_progress' || entry.status === 'completed'
        ? entry.status
        : 'pending';
    return [{ content: asText(entry.content), status }];
  });
  return {
    todos,
    counts: {
      pending: todos.filter((item) => item.status === 'pending').length,
      in_progress: todos.filter((item) => item.status === 'in_progress').length,
      completed: todos.filter((item) => item.status === 'completed').length,
    },
    revision: asNumber(latest.payload.revision),
  };
}

/** 最后一条「可能改变 run 状态」的事件 seq;没有则 0(调用方据此跳过失效)。 */
export function lastStatusEventSeq(events: readonly RunEvent[]): number {
  let seq = 0;
  for (const event of events) {
    if (event.type.startsWith('run.') && !STATUS_NEUTRAL_EVENTS.has(event.type)) {
      seq = event.seq;
    }
  }
  return seq;
}

/** 最后一条「计划可能变化」的事件 seq;没有则 0。 */
export function lastPlanEventSeq(events: readonly RunEvent[]): number {
  let seq = 0;
  for (const event of events) {
    if (PLAN_EVENTS.has(event.type)) {
      seq = event.seq;
    }
  }
  return seq;
}