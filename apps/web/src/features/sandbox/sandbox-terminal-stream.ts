/**
 * 沙箱终端流协议层:SSE 帧解析、终端控制序列清洗与可打断等待。
 *
 * 从 sandbox-terminal 抽出,保持纯函数、不依赖 React;渲染层只消费解析出的
 * SessionEvent。对外仍经 sandbox-terminal 再导出,既有 import 路径不变。
 */
import type { SseFrame } from '@/lib/sse';

/** 本地只留最近 200KB 输出,避免长时间运行把内存撑爆(后端 ring 另有上限)。 */
export const MAX_OUTPUT_CHARS = 200 * 1024;
export const MAX_RECONNECTS = 3;
export const RECONNECT_DELAYS = [500, 1000, 2000];
export const NEWLINE = '\n';
/** Ctrl-C / Ctrl-D:终端里按字节写回,不是命令。 */
export const CTRL_C = String.fromCharCode(3);
export const CTRL_D = String.fromCharCode(4);
const ESC = 27;
const BEL = 7;

/** 一个会话事件(SSE 帧解析后的展示形态)。 */
export type SessionEvent =
  | { seq: number; kind: 'output'; data: string }
  | { seq: number; kind: 'exit'; exitCode: number }
  | { seq: number; kind: 'error'; code: string; message: string };

export type Phase = 'connecting' | 'live' | 'reconnecting' | 'exited' | 'error';

/**
 * 剥掉 ANSI 控制序列与字节级回车。
 *
 * 只做「能安全贴进 <pre>」这一件事:CSI / OSC / 单字符转义整段丢弃,
 * \r 直接去掉(进度条会重复几行,但不会把界面写花)。非完整 xterm:
 * 不做光标定位与屏幕重绘,vim/top 这类全屏程序不适用。
 */
export function stripTerminalControls(text: string): string {
  let out = '';
  let index = 0;
  while (index < text.length) {
    const code = text.charCodeAt(index);
    if (code === ESC) {
      const next = text[index + 1];
      if (next === '[') {
        // CSI:参数/中间字节之后是 0x40-0x7E 的终结字节
        let cursor = index + 2;
        while (cursor < text.length) {
          const ch = text.charCodeAt(cursor);
          if (ch >= 0x40 && ch <= 0x7e) {
            cursor += 1;
            break;
          }
          cursor += 1;
        }
        index = cursor;
        continue;
      }
      if (next === ']') {
        // OSC:到 BEL 或 ESC(ESC \ 的 ST 由下一次循环吃掉);BEL 自身一并吞掉
        let cursor = index + 2;
        while (cursor < text.length) {
          const ch = text.charCodeAt(cursor);
          if (ch === ESC) break;
          cursor += 1;
          if (ch === BEL) break;
        }
        index = cursor;
        continue;
      }
      index += next === undefined ? 1 : 2;
      continue;
    }
    if (code === 13) {
      index += 1;
      continue;
    }
    out += text[index];
    index += 1;
  }
  return out;
}

/** SSE 帧 → 会话事件;帧里的 seq 与 id 行都用于断线续传。 */
export function parseSessionEvent(frame: SseFrame): SessionEvent | null {
  if (!frame.event) return null;
  let frameId = Number.NaN;
  for (const line of frame.raw.split(NEWLINE)) {
    if (line.indexOf('id:') === 0) {
      const parsed = Number.parseInt(line.slice(3).trim(), 10);
      if (Number.isFinite(parsed)) frameId = parsed;
    }
  }
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  const seq = typeof record.seq === 'number' ? record.seq : frameId;
  if (!Number.isFinite(seq)) return null;
  if (frame.event === 'output') {
    return {
      seq,
      kind: 'output',
      data: typeof record.data === 'string' ? record.data : '',
    };
  }
  if (frame.event === 'exit') {
    return {
      seq,
      kind: 'exit',
      exitCode: typeof record.exit_code === 'number' ? record.exit_code : -1,
    };
  }
  if (frame.event === 'error') {
    return {
      seq,
      kind: 'error',
      code:
        typeof record.code === 'string' ? record.code : 'sandbox.session_error',
      message: typeof record.message === 'string' ? record.message : '会话出错',
    };
  }
  return null;
}

/** 可被 AbortSignal 打断的等待(重连退避用)。 */
export function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    function onAbort() {
      clearTimeout(timer);
      resolve();
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort);
  });
}
