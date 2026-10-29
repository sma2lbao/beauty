/**
 * 沙箱持久 shell 会话的状态机:开会话 → 订阅 SSE(断线带 Last-Event-ID 退避重连)
 * → 写回 stdin / 清屏 / 手动重连。
 *
 * 渲染层只消费状态与动作,不碰协议细节;协议层纯函数见 ./sandbox-terminal-stream。
 */
import { useEffect, useRef, useState } from 'react';

import type { SandboxSession } from '@/lib/sandbox-api';
import {
  closeSandboxSession,
  openSandboxSession,
  sandboxSessionStreamUrl,
  sendSandboxSessionInput,
} from '@/lib/sandbox-api';
import { connectSse } from '@/lib/sse';
import type { SseFrame } from '@/lib/sse';

import type { Phase } from './sandbox-terminal-stream';
import {
  MAX_OUTPUT_CHARS,
  MAX_RECONNECTS,
  RECONNECT_DELAYS,
  parseSessionEvent,
  stripTerminalControls,
  wait,
} from './sandbox-terminal-stream';

export function useSandboxTerminalSession(sandboxId: string) {
  const [session, setSession] = useState<SandboxSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('connecting');
  const [output, setOutput] = useState('');
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [reconnectNonce, setReconnectNonce] = useState(0);

  const lastSeq = useRef(0);

  // 开会话:每个挂载实例开一个;卸载/重开时尽力关掉(失败交给空闲回收)。
  useEffect(() => {
    let cancelled = false;
    let opened: SandboxSession | null = null;

    openSandboxSession(sandboxId)
      .then((created) => {
        if (cancelled) {
          void closeSandboxSession(sandboxId, created.sessionId).catch(
            () => undefined,
          );
          return;
        }
        opened = created;
        setSession(created);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSessionError(
          error instanceof Error ? error.message : '会话终端不可用',
        );
      });

    return () => {
      cancelled = true;
      if (opened) {
        void closeSandboxSession(sandboxId, opened.sessionId).catch(
          () => undefined,
        );
      }
    };
  }, [sandboxId]);

  // 订阅输出流:断线指数退避重连(带 Last-Event-ID),exit/error 帧后停手。
  useEffect(() => {
    if (!session) return undefined;
    const sessionId = session.sessionId;
    const controller = new AbortController();
    let cancelled = false;
    let closed = false;

    function append(chunk: string) {
      if (!chunk) return;
      setOutput((current) => {
        const next = current + chunk;
        return next.length > MAX_OUTPUT_CHARS
          ? next.slice(next.length - MAX_OUTPUT_CHARS)
          : next;
      });
    }

    function handleFrame(frame: SseFrame) {
      const event = parseSessionEvent(frame);
      if (!event) return;
      if (event.seq > lastSeq.current) lastSeq.current = event.seq;
      if (event.kind === 'output') {
        append(stripTerminalControls(event.data));
        return;
      }
      closed = true;
      if (event.kind === 'exit') {
        setExitCode(event.exitCode);
        setPhase('exited');
        return;
      }
      setStreamError(event.code + ' · ' + event.message);
      setPhase('error');
    }

    async function run() {
      const url = sandboxSessionStreamUrl(sandboxId, sessionId);
      for (let attempt = 0; ; attempt += 1) {
        if (cancelled || closed) return;
        try {
          await connectSse(url, {
            lastEventId: lastSeq.current > 0 ? lastSeq.current : null,
            signal: controller.signal,
            onOpen: () => {
              if (cancelled || closed) return;
              setPhase('live');
              setStreamError(null);
            },
            onFrame: handleFrame,
          });
        } catch (error) {
          if (cancelled || closed || controller.signal.aborted) return;
          if (attempt >= MAX_RECONNECTS) {
            setPhase('error');
            setStreamError(
              error instanceof Error ? error.message : '终端连接已断开',
            );
            return;
          }
        }
        if (cancelled || closed) return;
        if (attempt >= MAX_RECONNECTS) {
          setPhase('error');
          setStreamError((current) => current ?? '终端连接已断开');
          return;
        }
        setPhase('reconnecting');
        await wait(
          RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)],
          controller.signal,
        );
      }
    }

    void run();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [sandboxId, session, reconnectNonce]);

  const interactive =
    Boolean(session) && phase !== 'exited' && phase !== 'error';

  async function writeInput(data: string) {
    if (!session || sending || !interactive) return;
    setSending(true);
    try {
      await sendSandboxSessionInput(sandboxId, session.sessionId, data);
    } catch (error) {
      setStreamError(error instanceof Error ? error.message : '输入发送失败');
    } finally {
      setSending(false);
    }
  }

  function reconnect() {
    setPhase('reconnecting');
    setStreamError(null);
    setReconnectNonce((value) => value + 1);
  }

  function clearOutput() {
    setOutput('');
  }

  return {
    session,
    sessionError,
    phase,
    output,
    exitCode,
    streamError,
    sending,
    interactive,
    writeInput,
    reconnect,
    clearOutput,
  };
}
