/**
 * 执行面板「终端」页签:会话沙箱里的持久 shell 终端。
 *
 * 挂载时开一个持久会话(真 TTY),输出走 SSE 流式追加,输入原样写回 stdin——
 * 所以 cd / 环境变量 / 逐行交互都能跨命令保留。这是沙箱里的 shell,不是本机终端。
 *
 * 能力不可用时(旧后端 / 会话被禁用)降级为单命令模式(每次 sh -lc 一条独立命令):
 * 界面上诚实标出「已降级」,而不是静默换一种行为。
 */
import { Loader2, RotateCw, Terminal, WifiOff } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { ConsoleButton } from '@/components/console/ui';
import type { SandboxExecResult, SandboxSession } from '@/lib/sandbox-api';
import {
  closeSandboxSession,
  execSandbox,
  openSandboxSession,
  sandboxSessionStreamUrl,
  sendSandboxSessionInput,
} from '@/lib/sandbox-api';
import { connectSse } from '@/lib/sse';
import type { SseFrame } from '@/lib/sse';
import { cn } from '@/lib/utils';

/** 本地只留最近 200KB 输出,避免长时间运行把内存撑爆(后端 ring 另有上限)。 */
const MAX_OUTPUT_CHARS = 200 * 1024;
const MAX_RECONNECTS = 3;
const RECONNECT_DELAYS = [500, 1000, 2000];
const NEWLINE = '\n';
/** Ctrl-C / Ctrl-D:终端里按字节写回,不是命令。 */
const CTRL_C = String.fromCharCode(3);
const CTRL_D = String.fromCharCode(4);
const ESC = 27;
const BEL = 7;

/** 一个会话事件(SSE 帧解析后的展示形态)。 */
type SessionEvent =
  | { seq: number; kind: 'output'; data: string }
  | { seq: number; kind: 'exit'; exitCode: number }
  | { seq: number; kind: 'error'; code: string; message: string };

type Phase = 'connecting' | 'live' | 'reconnecting' | 'exited' | 'error';

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
        typeof record.code === 'string'
          ? record.code
          : 'sandbox.session_error',
      message:
        typeof record.message === 'string' ? record.message : '会话出错',
    };
  }
  return null;
}

/** 可被 AbortSignal 打断的等待(重连退避用)。 */
function wait(ms: number, signal: AbortSignal): Promise<void> {
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

/**
 * 外层只负责「重开会话」的换 key:key 一变内层整块重挂载,状态自然归零,
 * 不在 effect 里同步 setState 重置(避免级联渲染)。
 */
export function SandboxTerminal({ sandboxId }: { sandboxId: string }) {
  const [restartNonce, setRestartNonce] = useState(0);
  return (
    <TerminalSession
      key={sandboxId + ':' + String(restartNonce)}
      sandboxId={sandboxId}
      onRestart={() => setRestartNonce((value) => value + 1)}
    />
  );
}

function TerminalSession({
  sandboxId,
  onRestart,
}: {
  sandboxId: string;
  onRestart: () => void;
}) {
  const [session, setSession] = useState<SandboxSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('connecting');
  const [output, setOutput] = useState('');
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [reconnectNonce, setReconnectNonce] = useState(0);

  const lastSeq = useRef(0);
  const scroller = useRef<HTMLDivElement | null>(null);

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

  // 输出增长后贴底:只改 scrollTop,不触发额外渲染。
  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [output]);

  const interactive = Boolean(session) && phase !== 'exited' && phase !== 'error';

  async function writeInput(data: string) {
    if (!session || sending || !interactive) return;
    setSending(true);
    try {
      await sendSandboxSessionInput(sandboxId, session.sessionId, data);
    } catch (error) {
      setStreamError(
        error instanceof Error ? error.message : '输入发送失败',
      );
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const line = input;
    setInput('');
    void writeInput(line + NEWLINE);
  }

  function restartSession() {
    onRestart();
  }

  if (sessionError) {
    return <SingleCommandTerminal sandboxId={sandboxId} reason={sessionError} />;
  }

  const promptLabel = session ? session.cwd : '/workspace';

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-0.5 pb-2">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold text-ha-dim">
          <Terminal aria-hidden="true" className="h-3.5 w-3.5" />
          沙箱内 shell · 非本机
        </span>
        <TerminalStatus phase={phase} exitCode={exitCode} />
      </div>

      <div
        ref={scroller}
        data-testid="sandbox-terminal-output"
        className="min-h-0 flex-1 overflow-y-auto rounded-md border border-ha-border bg-ha-code p-3 font-mono text-[12px] leading-5"
      >
        {output ? (
          <pre className="whitespace-pre-wrap break-all text-ha-text-2">
            {output}
          </pre>
        ) : (
          <div className="space-y-2 text-ha-dim">
            <p>
              {session
                ? '已连上沙箱 shell。输入命令后回车；cd 与环境变量会跨命令保留。'
                : '正在沙箱里打开 shell…'}
            </p>
            <p className="text-[11px] leading-5 text-ha-faint">
              纯文本渲染：不支持 vim/top 这类全屏程序与光标定位；
              长输出只保留最近 200KB。
            </p>
          </div>
        )}
      </div>

      {exitCode !== null ? (
        <p
          data-testid="sandbox-terminal-exit"
          className="mt-2 shrink-0 text-[11px] text-ha-warning"
        >
          shell 已退出（退出码 {exitCode}），点「重开会话」可以继续。
        </p>
      ) : null}
      {streamError ? (
        <p
          data-testid="sandbox-terminal-error"
          className="mt-2 shrink-0 break-all text-[11px] text-ha-danger"
        >
          {streamError}
        </p>
      ) : null}

      <form
        onSubmit={submit}
        className={cn(
          'mt-2 flex shrink-0 items-center gap-2 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-1.5',
          interactive ? 'focus-within:border-ha-accent' : 'opacity-70',
        )}
      >
        <span className="shrink-0 font-mono text-[12px] text-ha-success">
          {promptLabel} $
        </span>
        <input
          aria-label="终端命令"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={interactive ? '输入命令后回车' : '等待 shell 连接…'}
          spellCheck={false}
          autoComplete="off"
          disabled={!interactive}
          className="min-w-0 flex-1 bg-transparent font-mono text-[12px] text-ha-text-body outline-none placeholder:text-ha-faint disabled:cursor-not-allowed"
        />
        <ConsoleButton
          type="submit"
          size="sm"
          variant="ghost"
          disabled={!interactive || sending}
        >
          发送
        </ConsoleButton>
      </form>

      <div className="mt-2 flex shrink-0 flex-wrap items-center gap-1.5">
        <ConsoleButton
          size="sm"
          variant="secondary"
          disabled={!interactive || sending}
          onClick={() => void writeInput(CTRL_C)}
        >
          Ctrl-C
        </ConsoleButton>
        <ConsoleButton
          size="sm"
          variant="secondary"
          disabled={!interactive || sending}
          onClick={() => void writeInput(CTRL_D)}
        >
          Ctrl-D
        </ConsoleButton>
        <ConsoleButton
          size="sm"
          variant="ghost"
          onClick={() => setOutput('')}
          disabled={output.length === 0}
        >
          清屏
        </ConsoleButton>
        {phase === 'error' || phase === 'reconnecting' ? (
          <ConsoleButton
            size="sm"
            variant="secondary"
            onClick={() => {
              setPhase('reconnecting');
              setStreamError(null);
              setReconnectNonce((value) => value + 1);
            }}
          >
            <RotateCw aria-hidden="true" className="mr-1 h-3 w-3" />
            重连
          </ConsoleButton>
        ) : null}
        <ConsoleButton
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={restartSession}
        >
          重开会话
        </ConsoleButton>
      </div>
    </div>
  );
}

/** 连接状态:已连接 / 重连中 / 已退出(code) / 已断开。 */
function TerminalStatus({
  phase,
  exitCode,
}: {
  phase: Phase;
  exitCode: number | null;
}) {
  const [label, tone] =
    phase === 'live'
      ? ['已连接', 'text-ha-success']
      : phase === 'reconnecting'
        ? ['重连中…', 'text-ha-warning']
        : phase === 'exited'
          ? ['已退出（' + String(exitCode ?? 0) + '）', 'text-ha-warning']
          : phase === 'error'
            ? ['已断开', 'text-ha-danger']
            : ['连接中…', 'text-ha-dim'];
  return (
    <span
      data-testid="sandbox-terminal-status"
      className={cn('flex items-center gap-1.5 font-mono text-[11px]', tone)}
    >
      {phase === 'connecting' || phase === 'reconnecting' ? (
        <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
      ) : phase === 'error' ? (
        <WifiOff aria-hidden="true" className="h-3 w-3" />
      ) : (
        <span
          aria-hidden="true"
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            phase === 'live' ? 'bg-ha-success' : 'bg-ha-warning',
          )}
        />
      )}
      {label}
    </span>
  );
}

/**
 * 降级路径:开会话失败时退回单命令模式(每次独立 sh -lc)。
 *
 * 保留原来那套交互(输入一条、显示 stdout/stderr/退出码),并在顶部说明降级原因——
 * 用户至少还能验证产物,但别指望 cd 跨命令生效。
 */
function SingleCommandTerminal({
  sandboxId,
  reason,
}: {
  sandboxId: string;
  reason: string;
}) {
  const [entries, setEntries] = useState<CommandEntry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const scroller = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [entries]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const command = input.trim();
    if (!command || busy) return;
    const id = nextId.current;
    nextId.current += 1;
    setInput('');
    setBusy(true);
    setEntries((current) => [
      ...current,
      { id, command, status: 'running', result: null, error: null },
    ]);
    try {
      const result = await execSandbox(sandboxId, ['sh', '-lc', command]);
      setEntries((current) =>
        current.map((entry) =>
          entry.id === id
            ? { ...entry, status: 'done', result, error: null }
            : entry,
        ),
      );
    } catch (error) {
      setEntries((current) =>
        current.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                status: 'done',
                result: null,
                error: error instanceof Error ? error.message : '执行失败',
              }
            : entry,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 rounded-md border border-ha-warning-border bg-ha-warning-bg px-2.5 py-2 text-[11px] leading-5">
        <span className="font-semibold text-ha-warning">
          会话终端不可用，已降级为单命令模式
        </span>
        <span className="mt-0.5 block break-all text-ha-dim">
          原因：{reason}。每条命令独立执行，cd 不会跨命令生效。
        </span>
      </div>

      <div
        ref={scroller}
        data-testid="sandbox-terminal-output"
        className="mt-2 min-h-0 flex-1 overflow-y-auto rounded-md border border-ha-border bg-ha-code p-3 font-mono text-[12px] leading-5"
      >
        {entries.length === 0 ? (
          <div className="space-y-2 text-ha-dim">
            <p className="flex items-center gap-1.5">
              <Terminal aria-hidden="true" className="h-3.5 w-3.5" />
              这是沙箱内的验证终端
            </p>
            <p className="text-[11px] leading-5">
              命令在会话沙箱（/workspace）里执行，不是本机终端；需要时请写全路径。
            </p>
            <p className="text-[11px] text-ha-faint">
              例如：ls -la · cat README.md · python -V
            </p>
          </div>
        ) : (
          <ol className="space-y-3">
            {entries.map((entry) => (
              <li key={entry.id}>
                <div className="flex items-start gap-1.5 break-all text-ha-text-2">
                  <span className="shrink-0 text-ha-success">$</span>
                  <span className="min-w-0 flex-1 whitespace-pre-wrap">
                    {entry.command}
                  </span>
                  {entry.status === 'running' ? (
                    <Loader2 className="mt-1 h-3 w-3 shrink-0 animate-spin text-ha-info" />
                  ) : null}
                </div>
                {entry.result ? (
                  <div className="mt-1 space-y-1">
                    {entry.result.stdout ? (
                      <pre className="whitespace-pre-wrap break-all text-ha-text-2">
                        {entry.result.stdout}
                      </pre>
                    ) : null}
                    {entry.result.stderr ? (
                      <pre className="whitespace-pre-wrap break-all text-ha-danger">
                        {entry.result.stderr}
                      </pre>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span
                        className={cn(
                          entry.result.exitCode === 0
                            ? 'text-ha-success'
                            : 'text-ha-danger',
                        )}
                      >
                        退出码 {entry.result.exitCode}
                      </span>
                      <span className="text-ha-faint">
                        {entry.result.durationMs} ms
                      </span>
                      {entry.result.truncated ? (
                        <span className="text-ha-warning">
                          输出过长已截断
                        </span>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {entry.error ? (
                  <p className="mt-1 break-all text-ha-danger">{entry.error}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </div>

      <form
        onSubmit={submit}
        className="mt-2 flex shrink-0 items-center gap-2 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-1.5 focus-within:border-ha-accent"
      >
        <span className="shrink-0 font-mono text-[12px] text-ha-success">
          ~/workspace $
        </span>
        <input
          aria-label="终端命令"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入命令后回车"
          spellCheck={false}
          autoComplete="off"
          className="min-w-0 flex-1 bg-transparent font-mono text-[12px] text-ha-text-body outline-none placeholder:text-ha-faint"
        />
        <ConsoleButton
          type="submit"
          size="sm"
          variant="ghost"
          disabled={busy || input.trim().length === 0}
        >
          执行
        </ConsoleButton>
      </form>
    </div>
  );
}

type CommandEntry = {
  id: number;
  command: string;
  status: 'running' | 'done';
  result: SandboxExecResult | null;
  error: string | null;
};
