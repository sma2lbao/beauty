import { describe, expect, it } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';
import { mergeEvents } from '@/lib/runs-api';

const event: RunEvent = {
  seq: 3,
  type: 'run.planned.v1',
  payload: { attempt: 1 },
};

describe('mergeEvents', () => {
  it('去重并按 seq 升序', () => {
    const merged = mergeEvents([event], {
      seq: 2,
      type: 'run.created.v1',
      payload: {},
    });
    expect(merged.map((item) => item.seq)).toEqual([2, 3]);
  });

  it('重复 seq 不重复插入(SSE 重放/续传)', () => {
    const merged = mergeEvents([event], event);
    expect(merged).toHaveLength(1);
  });
});
