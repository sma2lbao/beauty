/**
 * 账号级任务状态流:订阅 `GET /api/v1/runs/stream`(SSE),把每个 run 的最新摘要
 * 直接 upsert 进 `['runs']` 缓存。
 *
 * 为什么不是"收到变化再拉列表":那等于把定时轮询换成事件触发的轮询,请求数不减。
 * 帧里已经带整份摘要(与 `GET /runs/{id}` 同形),落缓存即可,空闲期零请求。
 *
 * 契约(services/agent-orchestrator/agent_orchestrator/services/runs_stream.py):
 * - 帧 `id: <updated_at>|<run_id>`,`event: run.state.v1`,data 为
 *   `{"cursor": "<同 id>", "run": <RunRead>}`;
 * - 账号级长连接,服务端不发 `stream.end`;断线由本模块指数退避重连并带
 *   Last-Event-ID 续传。
 *
 * 兜底:连接未就绪时侧栏/看板仍按 30s 轮询(见 useRunsStreamStatus 的消费点),
 * 断网时 UI 不会变成死的。
 */
import type { QueryClient } from '@tanstack/react-query';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';

import { runsBase as BASE } from '@/config/env';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import type { Run } from '@/lib/runs-types';
import type { SseFrame } from '@/lib/sse';
import { connectSse } from '@/lib/sse';

/** 帧事件名;服务端常量在同名后端模块的 RUN_STATE_EVENT。 */
export const RUN_STATE_EVENT = 'run.state.v1';

/** 连接状态:connected 决定侧栏/看板是否还要轮询兜底。 */
export type RunsStreamStatus = { connected: boolean; reconnecting: boolean };

const DISCONNECTED: RunsStreamStatus = { connected: false, reconnecting: false };

let status: RunsStreamStatus = DISCONNECTED;
const listeners = new Set<() => void>();

function setStatus(next: RunsStreamStatus): void {
  if (
    next.connected === status.connected &&
    next.reconnecting === status.reconnecting
  ) {
    return;
  }
  status = next;
  for (const listener of listeners) listener();
}

/** 供 useSyncExternalStore 订阅(模块级单例,布局层一处挂载)。 */
export function subscribeRunsStreamStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 当前连接状态快照。 */
export function getRunsStreamStatus(): RunsStreamStatus {
  return status;
}

/** 读连接状态:未连上时调用方应保留轮询兜底。 */
export function useRunsStreamStatus(): RunsStreamStatus {
  return useSyncExternalStore(
    subscribeRunsStreamStatus,
    getRunsStreamStatus,
    getRunsStreamStatus,
  );
}

/** 解析一帧 run.state.v1;形状不对(非 JSON / 缺 run)返回 null 并跳过。 */
export function parseRunStateFrame(
  frame: Pick<SseFrame, 'event' | 'data'>,
): { cursor: string | null; run: Run } | null {
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (typeof payload !== 'object' || payload === null) return null;
  const record = payload as { cursor?: unknown; run?: unknown };
  if (typeof record.run !== 'object' || record.run === null) return null;
  return {
    cursor: typeof record.cursor === 'string' ? record.cursor : null,
    run: record.run as Run,
  };
}

/**
 * 把一条 run 摘要并进列表缓存:命中按 id 原地覆盖(保留旧对象里的未知字段),
 * 未命中则前插(服务端列表按 created_at 倒序,新 run 就在最前)。
 */
export function upsertRun(list: readonly Run[], run: Run): Run[] {
  const index = list.findIndex((item) => item.id === run.id);
  if (index < 0) return [run, ...list];
  const next = [...list];
  next[index] = { ...next[index], ...run };
  return next;
}

/**
 * 把一条摘要写进缓存。列表未加载过时不"凭空造一个只有一条的列表"
 * (那会让 useQuery 以为数据已就绪,永远不再拉全量),详情同理只在已有数据时合并。
 */
function applyRunState(queryClient: QueryClient, run: Run): void {
  queryClient.setQueryData<Run[]>(queryKeys.runs, (current) =>
    current ? upsertRun(current, run) : current,
  );
  queryClient.setQueryData<Run>(queryKeys.run(run.id), (current) =>
    current ? { ...current, ...run } : current,
  );
}

/**
 * 建立账号级状态流(挂在 ConsoleLayout 一处)。断线指数退避 1s→30s 重连,
 * 重连带上一帧的游标;卸载即断开。
 */
export function useRunsStream(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let cursor: string | null = null;

    function scheduleRetry(): void {
      if (cancelled) return;
      setStatus({ connected: false, reconnecting: true });
      const delay = Math.min(1000 * 2 ** attempt, 30_000);
      attempt += 1;
      retryTimer = setTimeout(() => void connect(), delay);
    }

    async function connect(): Promise<void> {
      if (cancelled) return;
      controller = new AbortController();
      try {
        await connectSse(BASE + '/api/v1/runs/stream', {
          lastEventId: cursor,
          signal: controller.signal,
          onOpen: () => {
            attempt = 0;
            setStatus({ connected: true, reconnecting: false });
          },
          onFrame: (frame) => {
            if (frame.event !== RUN_STATE_EVENT) return;
            const state = parseRunStateFrame(frame);
            if (!state) return;
            if (state.cursor) cursor = state.cursor;
            applyRunState(queryClient, state.run);
          },
          // 账号级流没有 stream.end:读完就是中断,交给重连(带游标)。
          onInterrupted: () => {
            throw new ApiError(0, 'stream.interrupted', '任务状态流意外中断');
          },
          mapError: (response) =>
            new ApiError(
              response.status,
              'stream_failed',
              '任务状态流连接失败(' + response.status + ')',
            ),
        });
        scheduleRetry();
      } catch (error) {
        if (cancelled || (error as Error)?.name === 'AbortError') return;
        scheduleRetry();
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retryTimer !== undefined) clearTimeout(retryTimer);
      setStatus(DISCONNECTED);
    };
  }, [queryClient]);
}
