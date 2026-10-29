import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';

import { useRunEvents } from './use-run-events';

const { streamRunEventsMock } = vi.hoisted(() => ({
  streamRunEventsMock: vi.fn(),
}));

vi.mock('@/lib/runs-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return { ...actual, streamRunEvents: streamRunEventsMock };
});

const event = (seq: number): RunEvent => ({
  seq,
  type: 'run.tool.called.v1',
  payload: { name: 'read_file', ok: true },
});

beforeEach(() => {
  vi.useFakeTimers();
  streamRunEventsMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useRunEvents', () => {
  it('断线后带 Last-Event-ID 重连,事件去重累积', async () => {
    streamRunEventsMock
      .mockImplementationOnce(
        async (
          _runId: string,
          handlers: {
            onOpen?: () => void;
            onEvent?: (item: RunEvent) => void;
          },
        ) => {
          handlers.onOpen?.();
          handlers.onEvent?.(event(1));
          throw new Error('连接断开');
        },
      )
      .mockImplementationOnce(
        async (
          _runId: string,
          handlers: {
            onOpen?: () => void;
            onEvent?: (item: RunEvent) => void;
            onEnd?: () => void;
          },
        ) => {
          handlers.onOpen?.();
          handlers.onEvent?.(event(2));
          handlers.onEnd?.();
        },
      );

    const { result } = renderHook(() => useRunEvents('run-1'));

    await act(async () => {
      await Promise.resolve();
    });
    expect(streamRunEventsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(streamRunEventsMock).toHaveBeenCalledTimes(2);
    const secondCall = streamRunEventsMock.mock.calls[1] as [
      string,
      { lastEventId: number | null },
    ];
    expect(secondCall[1].lastEventId).toBe(1);
    expect(result.current.events.map((item) => item.seq)).toEqual([1, 2]);
    expect(result.current.connected).toBe(false);
    expect(result.current.reconnecting).toBe(false);
  });
});
