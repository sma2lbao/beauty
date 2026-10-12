import { useEffect, useState } from 'react';

import type { RunEvent } from '@/lib/runs-api';
import { mergeEvents, streamRunEvents } from '@/lib/runs-api';

/**
 * 订阅某个 run 的 SSE 事件流:切换 run 即重连,卸载即断开。
 *
 * 不缓存跨 run 的事件(与当前 runId 绑定),所以 effect 里无需同步清空状态——
 * 这也是 React Compiler 的 set-state-in-effect 规则要求的写法。
 */
export function useRunEvents(runId: string | null) {
  const [feed, setFeed] = useState<{
    runId: string | null;
    events: RunEvent[];
  }>({ runId: null, events: [] });
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!runId) {
      return;
    }
    const controller = new AbortController();
    streamRunEvents(runId, {
      signal: controller.signal,
      onOpen: () => setConnected(true),
      onEvent: (event) =>
        setFeed((previous) =>
          previous.runId === runId
            ? { runId, events: mergeEvents(previous.events, event) }
            : { runId, events: [event] },
        ),
      onEnd: () => setConnected(false),
    }).catch(() => setConnected(false));
    return () => {
      controller.abort();
    };
  }, [runId]);

  const events = feed.runId === runId ? feed.events : [];
  return { events, connected };
}
