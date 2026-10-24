/**
 * SSE 基座:手写 frame 解析(fetch + ReadableStream),供 runs/conversations/notifications 共用。
 *
 * 为什么不用 `EventSource`:需要带 Bearer/CSRF 头与 `credentials: 'include'`,
 * EventSource 不支持自定义请求头;接续(Last-Event-ID)/`stream.end` 语义也需自己掌握。
 *
 * 帧语义(与后端契约保持一致):
 * - 帧以空行(`\n\n`)分隔,跨分片到达时由调用方缓冲区拼齐;
 * - `event:` 标注事件名,`data:` 可多行(按 `\n` 拼接);
 * - `:` 开头的注释行(心跳)跳过;
 * - `event: stream.end` 表示服务端正常收尾,不再回调后续帧。
 */

import { ApiError, jsonHeaders } from '@/lib/http';

/** 解析后的一个 SSE 帧。 */
export type SseFrame = {
  /** `event:` 字段;缺省(null)表示默认事件。 */
  event: string | null;
  /** 所有 `data:` 行按换行拼接后的负载。 */
  data: string;
  /** 原始帧文本(未裁剪),便于调用方复用各自的按行解析器。 */
  raw: string;
};

/** 读流的回调:帧/收尾/中断三件事。 */
export type SseReadHandlers = {
  /** 一个完整帧(已排除注释行与 `stream.end`)。 */
  onFrame?: (frame: SseFrame) => void;
  /** 收到 `event: stream.end`:服务端正常收尾。 */
  onEnd?: () => void;
  /** 没等到 `stream.end` 连接就断了(网络中断);不传则按正常结束处理。 */
  onInterrupted?: () => void;
};

/**
 * 解析一个帧文本:提取 event/data 行,忽略注释行。
 * 既没有 event 也没有 data(纯注释/空帧)时返回 null。
 */
export function parseSseFrameText(raw: string): SseFrame | null {
  let event: string | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (!line || line.startsWith(':')) {
      continue;
    }
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      const value = line.slice('data:'.length);
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value);
    }
  }
  if (!event && dataLines.length === 0) {
    return null;
  }
  return { event, data: dataLines.join('\n'), raw };
}

/**
 * 消费一个 SSE 响应体:按 `\n\n` 切帧并逐帧回调。
 *
 * - `stream.end` 帧只触发 `onEnd`,不会进 `onFrame`;
 * - 读完仍没有 `stream.end` 时触发 `onInterrupted`(runs 据此抛
 *   `stream.interrupted` 交给上层带 Last-Event-ID 重连);
 * - 没有响应体时按统一错误契约抛 `ApiError`。
 */
export async function readSseStream(
  response: Response,
  handlers: SseReadHandlers = {},
): Promise<void> {
  const body = response.body;
  if (!body) {
    throw new ApiError(response.status, 'stream_failed', '响应没有流式内容');
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      handlers.onInterrupted?.();
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const raw of frames) {
      const frame = parseSseFrameText(raw);
      if (!frame) {
        continue;
      }
      if (frame.event === 'stream.end') {
        handlers.onEnd?.();
        return;
      }
      handlers.onFrame?.(frame);
    }
  }
}

/** 连接失败时的错误映射;缺省按统一错误契约给出通用文案。 */
function defaultMapError(response: Response): ApiError {
  return new ApiError(
    response.status,
    'stream_failed',
    `流式连接失败(${response.status})`,
  );
}

/** `connectSse` 的选项:请求构造 + 流回调。 */
export type ConnectSseOptions = {
  /** 额外请求头;Accept: text/event-stream 由基座强制声明。 */
  headers?: Record<string, string>;
  /** 断线续传游标;非空时写 Last-Event-ID。 */
  lastEventId?: string | number | null;
  signal?: AbortSignal;
  method?: string;
  body?: BodyInit | null;
  /** 开流成功(2xx 且有响应体)后回调。 */
  onOpen?: () => void;
  onFrame?: (frame: SseFrame) => void;
  onEnd?: () => void;
  onInterrupted?: () => void;
  /** 连接失败(`!ok` 或没有响应体)时映射成各调用方自己的错误 code/文案。 */
  mapError?: (response: Response) => ApiError | Promise<ApiError>;
};

/**
 * 发起一个 SSE 请求并把响应交给 `readSseStream`。
 *
 * 统一负责:带鉴权/CSRF 头、`Accept: text/event-stream`(BFF 据此流式转发,
 * 否则会缓冲到读超时)、`credentials: 'include'`、`Last-Event-ID`、
 * 以及连接失败时的错误映射。断流回调(onInterrupted)由调用方决定语义。
 */
export async function connectSse(
  url: string,
  options: ConnectSseOptions = {},
): Promise<void> {
  const headers: Record<string, string> = {
    ...(await jsonHeaders()),
    ...options.headers,
  };
  headers.Accept = 'text/event-stream';
  if (options.lastEventId) {
    headers['Last-Event-ID'] = String(options.lastEventId);
  }
  const response = await fetch(url, {
    method: options.method,
    body: options.body,
    credentials: 'include',
    headers,
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    throw await (options.mapError ?? defaultMapError)(response);
  }
  options.onOpen?.();
  await readSseStream(response, {
    onFrame: options.onFrame,
    onEnd: options.onEnd,
    onInterrupted: options.onInterrupted,
  });
}
