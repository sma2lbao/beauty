import { describe, expect, it } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';
import { pendingWritesFrom } from '@/lib/presentation';

function confirmation(
  seq: number,
  writes: unknown,
  type = 'run.confirmation.required.v1',
): RunEvent {
  return { seq, type, payload: { pending_writes: writes } };
}

describe('pendingWritesFrom', () => {
  it('解析最近一次确认中断的待办操作', () => {
    const writes = pendingWritesFrom([
      confirmation(1, [{ id: 'a', name: 'publish_post', arguments: '{}' }]),
      confirmation(2, [
        { id: 'b', name: 'delete_file', arguments: '{"path":"x.md"}' },
      ]),
    ]);

    expect(writes).toEqual([
      { id: 'b', name: 'delete_file', arguments: '{"path":"x.md"}' },
    ]);
  });

  it('没有确认事件时返回空数组', () => {
    expect(pendingWritesFrom([])).toEqual([]);
    expect(
      pendingWritesFrom([{ seq: 1, type: 'run.planned.v1', payload: {} }]),
    ).toEqual([]);
  });

  it('丢弃缺工具名/非对象的异常项,不抛错', () => {
    const writes = pendingWritesFrom([
      confirmation(1, [
        null,
        'publish_post',
        { id: 'a' },
        { id: 'b', name: '  ' },
        { id: 'c', name: 'delete_file', arguments: { target: 'x' } },
      ]),
    ]);

    // arguments 非字符串时按空串处理(展示层再兜底成「无参数」)
    expect(writes).toEqual([{ id: 'c', name: 'delete_file', arguments: '' }]);
  });

  it('pending_writes 不是数组时返回空数组', () => {
    expect(pendingWritesFrom([confirmation(1, { name: 'x' })])).toEqual([]);
  });
});
