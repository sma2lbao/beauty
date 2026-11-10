/**
 * 会话 SSE 流式回合的公共开流器:发消息(/messages/stream)与
 * 编辑重发 / 重新生成(/messages/{id}/rewrite)共用同一套帧解析与错误契约,
 * 解析器只在这里维护一份(契约见 docs/design/interaction-closure.md §2)。
 */

import type { StreamMessageHandlers } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { connectSse, parseSseFrameText } from '@/lib/sse';
import { toolCallMetaFrom } from '@/lib/tool-meta';

/** 开流失败(HTTP 非 2xx / 没有流式 body)时的兜底 code 与文案。 */
export type StreamTurnFallback = {
  /** 错误体没有 code 时的兜底错误码。 */
  code: string;
  /** HTTP 200 但没有流式内容。 */
  emptyMessage: string;
  /** 错误体没有 message 时的兜底文案(带状态码)。 */
  failureMessage: (status: number) => string;
};

/** 解析一个具名 SSE 帧:`event: 名字` + `data: JSON`。 */
export function parseSseFrame(
  frame: string,
): [string, Record<string, unknown>] | null {
  const parsed = parseSseFrameText(frame);
  if (!parsed?.event || !parsed.data) {
    return null;
  }
  try {
    return [parsed.event, JSON.parse(parsed.data) as Record<string, unknown>];
  } catch {
    return null;
  }
}

/** 按事件名分发一帧(未知事件忽略,便于后端新增帧而不破坏旧前端)。 */
function dispatchFrame(handlers: StreamMessageHandlers, raw: string): void {
  const parsed = parseSseFrame(raw);
  if (!parsed) {
    return;
  }
  const [name, data] = parsed;
  if (name === 'meta') {
    handlers.onMeta?.(
      data as Parameters<NonNullable<StreamMessageHandlers['onMeta']>>[0],
    );
  } else if (name === 'delta') {
    handlers.onDelta?.(typeof data.text === 'string' ? data.text : '');
  } else if (name === 'tool') {
    if (typeof data.name === 'string') {
      handlers.onTool?.({
        name: data.name,
        arguments: typeof data.arguments === 'string' ? data.arguments : '',
        ok: data.ok !== false,
        ...toolCallMetaFrom(data),
      });
    }
  } else if (name === 'done') {
    handlers.onDone?.(
      data as Parameters<NonNullable<StreamMessageHandlers['onDone']>>[0],
    );
  } else if (name === 'error') {
    handlers.onError?.({
      code: typeof data.code === 'string' ? data.code : 'llm.upstream_error',
      message: typeof data.message === 'string' ? data.message : '模型调用失败',
    });
  }
}

/**
 * 开一条 POST SSE 流并把帧分发给 handlers。
 *
 * 开流前的错误(404/409/422)仍是统一错误契约,直接抛 ApiError;
 * 开流后的失败由后端发 error 帧,走 onError(HTTP 已是 200,改不了)。
 */
export async function streamTurn(
  path: string,
  body: string,
  handlers: StreamMessageHandlers,
  fallback: StreamTurnFallback,
): Promise<void> {
  await connectSse(path, {
    method: 'POST',
    body,
    signal: handlers.signal,
    mapError: async (response) => {
      // HTTP 层已是 200 但没流式内容;或开流前失败(404/409/422)。
      if (response.ok) {
        return new ApiError(response.status, 'stream_failed', fallback.emptyMessage);
      }
      let code = fallback.code;
      let message = fallback.failureMessage(response.status);
      try {
        const parsed = (await response.json()) as {
          code?: string;
          message?: string;
        };
        code = parsed.code ?? code;
        message = parsed.message ?? message;
      } catch {
        // 非 JSON 错误体:保留兜底文案
      }
      return new ApiError(response.status, code, message);
    },
    onFrame: (frame) => dispatchFrame(handlers, frame.raw),
  });
}
