/**
 * 读模型投影:过程项的折叠规则必须与服务端 `GET /runs/{id}/items` 一致
 * (services/agent-orchestrator/agent_orchestrator/services/items.py::list_items),
 * 否则「连上用事件、断线用端点」两套来源会给出不同的项。
 */
import { describe, expect, it } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';

import {
  itemsFrom,
  lastSubagentEventSeq,
  lastUsageEventSeq,
  stepsFrom,
  todosFrom,
} from './projections';

function event(seq: number, type: string, payload: Record<string, unknown>) {
  return { seq, type, payload } satisfies RunEvent;
}

const baseItem = {
  item_id: 'run-1:1:tool:1',
  thread_id: 'thread-1',
  turn_id: 'run-1',
  kind: 'tool',
  node: 'execute',
  attempt: 1,
  title: '读取配置',
  started_at: '2026-10-01T00:00:00Z',
};

describe('itemsFrom', () => {
  it('按 item_id 折叠相位:顺序取首现 seq,终态与 data 取最新', () => {
    const items = itemsFrom([
      event(3, 'run.item.v1', {
        ...baseItem,
        phase: 'started',
        step_id: null,
        data: { name: 'read_file' },
      }),
      event(4, 'run.item.v1', { ...baseItem, phase: 'delta' }),
      event(7, 'run.item.v1', {
        ...baseItem,
        phase: 'completed',
        step_id: 'run-1:1:tool:1',
        finished_at: '2026-10-01T00:00:03Z',
        duration_ms: 3000,
        status: 'completed',
        data: { ok: true },
      }),
    ]);

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      item_id: 'run-1:1:tool:1',
      seq: 3,
      phase: 'completed',
      status: 'completed',
      step_id: 'run-1:1:tool:1',
      finished_at: '2026-10-01T00:00:03Z',
      duration_ms: 3000,
      data: { name: 'read_file', ok: true },
    });
  });

  it('started/delta 相位投影为 running,denied 保留', () => {
    const items = itemsFrom([
      event(1, 'run.item.v1', { ...baseItem, phase: 'started' }),
      event(2, 'run.item.v1', {
        ...baseItem,
        item_id: 'run-1:1:tool:2',
        phase: 'completed',
        status: 'denied',
      }),
    ]);

    expect(items.map((item) => item.status)).toEqual(['running', 'denied']);
  });

  it('忽略非 item 事件与缺 item_id 的帧,未知 kind 回落 tool', () => {
    const items = itemsFrom([
      event(1, 'run.step.v1', { node: 'plan' }),
      event(2, 'run.item.v1', { ...baseItem, item_id: '' }),
      event(3, 'run.item.v1', {
        ...baseItem,
        item_id: 'run-1:1:mystery:1',
        kind: 'mystery',
        phase: 'completed',
      }),
    ]);

    expect(items.map((item) => item.item_id)).toEqual(['run-1:1:mystery:1']);
    expect(items[0].kind).toBe('tool');
  });
});

describe('读模型与事件定位器', () => {
  it('步骤投影只取 run.step.v1,缺失字段兜底', () => {
    const steps = stepsFrom([
      event(1, 'run.step.v1', { step_id: 's1', node: 'plan', status: 'cancelled' }),
      event(2, 'run.item.v1', baseItem),
    ]);

    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      seq: 1,
      step_id: 's1',
      node: 'plan',
      status: 'cancelled',
      attempt: 0,
      round: 0,
    });
  });

  it('清单投影取最后一条全量快照并在客户端重算 counts', () => {
    const todos = todosFrom([
      event(1, 'run.todo.updated.v1', {
        todos: [{ content: 'a', status: 'completed' }],
        revision: 1,
      }),
      event(2, 'run.todo.updated.v1', {
        todos: [
          { content: 'a', status: 'completed' },
          { content: 'b', status: 'in_progress' },
        ],
        revision: 2,
      }),
    ]);

    expect(todos.revision).toBe(2);
    expect(todos.counts).toEqual({
      pending: 0,
      in_progress: 1,
      completed: 1,
    });
  });

  it('用量与子代理定位器只认各自事件', () => {
    const events = [
      event(1, 'run.usage.v1', {}),
      event(2, 'run.step.v1', {}),
      event(3, 'run.subagent.waiting.v1', {}),
      event(4, 'run.subagent.resumed.v1', {}),
      event(5, 'run.completed.v1', {}),
    ];

    expect(lastUsageEventSeq(events)).toBe(1);
    expect(lastSubagentEventSeq(events)).toBe(4);
    expect(lastUsageEventSeq([])).toBe(0);
    expect(lastSubagentEventSeq([])).toBe(0);
  });
});
