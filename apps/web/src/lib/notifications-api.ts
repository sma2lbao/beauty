/**
 * 通知流客户端(P1-9b):订阅 agent-orchestrator 的 /api/v1/notifications/stream。
 *
 * 复用 @/lib/sse 基座(runs-api 同构):手写 fetch + ReadableStream 而不用
 * EventSource —— 需要带 Bearer 头与 credentials,EventSource 做不到;
 * 注释行(心跳)由基座跳过。连接失败抛 ApiError,由调用方决定降级。
 */
import { ApiError } from '@/lib/http';
import { connectSse } from '@/lib/sse';
import { runsBase as BASE } from '@/config/env';

export type RunStatusNotification = {
  run_id: string;
  conversation_id: string | null;
  title: string;
  goal: string;
  status: string;
  updated_at: string | null;
};

const NEWLINE = String.fromCharCode(10);

function parseData(frame: string): RunStatusNotification | null {
  for (const line of frame.split(NEWLINE)) {
    if (!line.startsWith('data: ')) continue;
    try {
      return JSON.parse(line.slice(6)) as RunStatusNotification;
    } catch {
      return null;
    }
  }
  return null;
}

/** 订阅租户级通知;连接失败抛 ApiError,由调用方决定降级。 */
export async function streamNotifications(handlers: {
  onOpen?: () => void;
  onNotification: (notification: RunStatusNotification) => void;
  signal?: AbortSignal;
}): Promise<void> {
  await connectSse(`${BASE}/api/v1/notifications/stream`, {
    signal: handlers.signal,
    onOpen: handlers.onOpen,
    mapError: (response) =>
      new ApiError(
        response.status,
        'notifications.stream_failed',
        `通知流连接失败(${response.status})`,
      ),
    onFrame: (frame) => {
      const notification = parseData(frame.raw);
      if (notification) {
        handlers.onNotification(notification);
      }
    },
  });
}
