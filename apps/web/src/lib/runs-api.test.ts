import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';
import {
  ApiError,
  mergeEvents,
  parseEventFrame,
  runsApi,
  streamRunEvents,
} from '@/lib/runs-api';

const event: RunEvent = {
  seq: 3,
  type: 'run.planned.v1',
  payload: { attempt: 1 },
};

describe('parseEventFrame', () => {
  it('解析 data 行里的 RunEventRead', () => {
    const frame = [
      'id: 3',
      'event: run.planned.v1',
      `data: ${JSON.stringify({ ...event, run_id: 'r1', id: 'abc' })}`,
    ].join('\n');

    expect(parseEventFrame(frame)).toEqual({
      seq: 3,
      type: 'run.planned.v1',
      payload: { attempt: 1 },
      occurred_at: undefined,
    });
  });

  it('stream.end 帧没有 seq → null', () => {
    expect(parseEventFrame('event: stream.end\ndata: {"status":"completed"}')).toBeNull();
  });

  it('坏 JSON / 空 data → null(不抛)', () => {
    expect(parseEventFrame('event: x\ndata: {oops')).toBeNull();
    expect(parseEventFrame(': keep-alive')).toBeNull();
  });
});

describe('mergeEvents', () => {
  it('去重并按 seq 升序', () => {
    const merged = mergeEvents([event], { seq: 2, type: 'run.created.v1', payload: {} });
    expect(merged.map((item) => item.seq)).toEqual([2, 3]);
  });

  it('重复 seq 不重复插入(SSE 重放/续传)', () => {
    const merged = mergeEvents([event], event);
    expect(merged).toHaveLength(1);
  });
});

describe('runsApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('携带租户头并返回 JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'r1', status: 'pending' }],
    });
    vi.stubGlobal('fetch', fetchMock);

    const runs = await runsApi.list();

    expect(runs).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs?limit=50');
    expect((init.headers as Record<string, string>)['X-Tenant-Id']).toBe(
      'demo-tenant',
    );
  });

  it('后端错误契约映射为 ApiError(code/message)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ code: 'run.invalid_state', message: 'run 状态不允许' }),
      }),
    );

    await expect(runsApi.approve('r1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'run.invalid_state',
      message: 'run 状态不允许',
    });
  });

  it('非 JSON 错误体回退通用文案', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error('not json');
        },
      }),
    );

    await expect(runsApi.get('r1')).rejects.toBeInstanceOf(ApiError);
  });
});

describe('streamRunEvents', () => {
  it('按帧推送事件,stream.end 时收尾', async () => {
    const encoder = new TextEncoder();
    const payload = [
      `id: 1\nevent: run.created.v1\ndata: ${JSON.stringify({ seq: 1, type: 'run.created.v1', payload: {} })}\n\n`,
      `id: 2\nevent: run.completed.v1\ndata: ${JSON.stringify({ seq: 2, type: 'run.completed.v1', payload: {} })}\n\n`,
      'event: stream.end\ndata: {"status":"completed"}\n\n',
    ].join('');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(payload));
            controller.close();
          },
        }),
      }),
    );
    const received: number[] = [];
    const onEnd = vi.fn();
    const onOpen = vi.fn();

    await streamRunEvents('r1', {
      onEvent: (item) => received.push(item.seq),
      onOpen,
      onEnd,
    });

    expect(onOpen).toHaveBeenCalledOnce();
    expect(received).toEqual([1, 2]);
    expect(onEnd).toHaveBeenCalledOnce();
  });
});
