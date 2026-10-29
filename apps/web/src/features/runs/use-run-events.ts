import { useEffect, useState } from 'react';

import type { RunEvent } from '@/lib/runs-api';
import { mergeEvents, streamRunEvents } from '@/lib/runs-api';

type LinkPhase = 'idle' | 'connected' | 'reconnecting' | 'closed';

/**
 * 订阅某个 run 的 SSE 事件流。
 *
 * M0-07:断线自动重连,并带 Last-Event-ID(最大 seq)续传:
 * - 服务端正常 stream.end → 停止,不再重连;
 * - 网络中断/无 stream.end 的关闭 → 指数退避(1s/2s/4s…上限 10s)重连;
 * - 重连成功后重置退避;切 run/卸载即 abort。
 *
 * 状态按 runId 绑定:切换 run 时旧连接的事件不会串到新 run。
 */
export function useRunEvents(runId: string | null) {
  const [feed, setFeed] = useState<{
    runId: string | null;
    events: RunEvent[];
  }>({ runId: null, events: [] });
  const [link, setLink] = useState<{
    runId: string | null;
    phase: LinkPhase;
  }>({ runId: null, phase: 'idle' });

  useEffect(() => {
    if (!runId) {
      return;
    }
    let cancelled = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let lastEventId: number | null = null;

    function scheduleRetry() {
      if (cancelled) {
        return;
      }
      const delay = Math.min(1000 * 2 ** attempt, 10_000);
      attempt += 1;
      setLink({ runId, phase: 'reconnecting' });
      retryTimer = setTimeout(() => {
        void connect();
      }, delay);
    }

    async function connect(): Promise<void> {
      if (cancelled) {
        return;
      }
      controller = new AbortController();
      try {
        await streamRunEvents(runId as string, {
          signal: controller.signal,
          lastEventId,
          onOpen: () => {
            attempt = 0;
            setLink({ runId, phase: 'connected' });
          },
          onEvent: (event) => {
            lastEventId = event.seq;
            setFeed((previous) =>
              previous.runId === runId
                ? { runId, events: mergeEvents(previous.events, event) }
                : { runId, events: [event] },
            );
          },
          onEnd: () => {
            if (!cancelled) {
              setLink({ runId, phase: 'closed' });
            }
          },
        });
      } catch (error) {
        if (cancelled || (error as Error)?.name === 'AbortError') {
          return;
        }
        scheduleRetry();
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retryTimer !== undefined) {
        clearTimeout(retryTimer);
      }
    };
  }, [runId]);

  const events = feed.runId === runId ? feed.events : [];
  return {
    events,
    connected: link.runId === runId && link.phase === 'connected',
    reconnecting: link.runId === runId && link.phase === 'reconnecting',
  };
}
