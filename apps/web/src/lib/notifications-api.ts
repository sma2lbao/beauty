/**
 * 通知流客户端(P1-9b):订阅 agent-orchestrator 的 /api/v1/notifications/stream。
 *
 * 与 runs-api 的 SSE 客户端同构(手写 frame 解析,不用 EventSource:
 * 需要带 Bearer 头与 credentials,EventSource 做不到)。
 */
import { ApiError, jsonHeaders } from '@/lib/http';

const BASE = (import.meta.env.VITE_RUNS_API_URL as string | undefined) ?? '';

export type RunStatusNotification = {
  run_id: string;
  conversation_id: string | null;
  title: string;
  goal: string;
  status: string;
  updated_at: string | null;
};

const NEWLINE = String.fromCharCode(10);
const FRAME_SEP = NEWLINE + NEWLINE;

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
  const response = await fetch(`${BASE}/api/v1/notifications/stream`, {
    credentials: 'include',
    headers: await jsonHeaders(),
    signal: handlers.signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      'notifications.stream_failed',
      `通知流连接失败(${response.status})`,
    );
  }
  handlers.onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(FRAME_SEP);
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      if (!frame.trim() || frame.startsWith(':')) continue;
      const notification = parseData(frame);
      if (notification) handlers.onNotification(notification);
    }
  }
}