/**
 * 沙箱持久 shell 的终端界面:状态条、输出区、输入栏与快捷键按钮。
 *
 * 会话与流的状态由 useSandboxTerminalSession 持有;这里只管渲染、贴底滚动与
 * 本地输入框。降级路径见 ./single-command-terminal。
 */
import { Loader2, RotateCw, Terminal, WifiOff } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { ConsoleButton } from '@/components/console/ui';
import { cn } from '@/lib/utils';

import type { Phase } from './sandbox-terminal-stream';
import { CTRL_C, CTRL_D, NEWLINE } from './sandbox-terminal-stream';
import { SingleCommandTerminal } from './single-command-terminal';
import { useSandboxTerminalSession } from './use-sandbox-terminal-session';

export function TerminalSession({
  sandboxId,
  onRestart,
}: {
  sandboxId: string;
  onRestart: () => void;
}) {
  const {
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
  } = useSandboxTerminalSession(sandboxId);
  const [input, setInput] = useState('');
  const scroller = useRef<HTMLDivElement | null>(null);

  // 输出增长后贴底:只改 scrollTop,不触发额外渲染。
  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [output]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const line = input;
    setInput('');
    void writeInput(line + NEWLINE);
  }

  if (sessionError) {
    return (
      <SingleCommandTerminal sandboxId={sandboxId} reason={sessionError} />
    );
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
          onClick={clearOutput}
          disabled={output.length === 0}
        >
          清屏
        </ConsoleButton>
        {phase === 'error' || phase === 'reconnecting' ? (
          <ConsoleButton size="sm" variant="secondary" onClick={reconnect}>
            <RotateCw aria-hidden="true" className="mr-1 h-3 w-3" />
            重连
          </ConsoleButton>
        ) : null}
        <ConsoleButton
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={onRestart}
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
