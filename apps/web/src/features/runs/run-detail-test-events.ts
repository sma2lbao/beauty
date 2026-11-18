/**
 * 详情抽屉的测试夹具:把「读模型」反向拼成 run 事件流。
 *
 * 步骤/过程项/清单现在由事件流投影得出(事件分类学 §11/§13),所以断言这些面板的用例
 * 得给事件,而不是给 `runsApi.steps/items/todos` 的返回值。拼装规则与后端发射点
 * 一致:`run.step.v1` 每节点一条、`run.item.v1` 每相位一条、`run.todo.updated.v1`
 * 的 payload 是全量快照。
 */
import type { RunEvent, RunItem, RunStep, RunTodoItem } from '@/lib/runs-api';

export function stepEvent(step: RunStep): RunEvent {
  return { seq: step.seq, type: 'run.step.v1', payload: { ...step } };
}

export function todoEvent(todos: RunTodoItem[]): RunEvent {
  const counts = {
    pending: todos.filter((item) => item.status === 'pending').length,
    in_progress: todos.filter((item) => item.status === 'in_progress').length,
    completed: todos.filter((item) => item.status === 'completed').length,
  };
  return {
    seq: 1,
    type: 'run.todo.updated.v1',
    payload: { todos, counts, revision: 1 },
  };
}

export function itemEvent(item: RunItem): RunEvent {
  return { seq: item.seq, type: 'run.item.v1', payload: { ...item } };
}

/** 一次把三个读模型拼成事件流;空数组不产生事件。 */
export function readModelEvents({
  steps = [],
  todos = [],
  items = [],
}: {
  steps?: RunStep[];
  todos?: RunTodoItem[];
  items?: RunItem[];
} = {}): RunEvent[] {
  return [
    ...steps.map(stepEvent),
    ...(todos.length > 0 ? [todoEvent(todos)] : []),
    ...items.map(itemEvent),
  ];
}
