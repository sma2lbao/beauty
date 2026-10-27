/**
 * 执行面板「终端」页签:会话沙箱里的验证终端。
 *
 * 不是本机终端:命令经 workspace-service 的 /exec 在沙箱容器里跑,
 * 用来验证产物(ls / cat / npm test ...)。每次提交是一条独立命令,
 * 不保留 shell 会话状态,所以文案要讲清楚「cd 不跨条生效」。
 */
import { Loader2, Terminal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { ConsoleButton } from '@/components/console/ui';
import type { SandboxExecResult } from '@/lib/sandbox-api';
import { execSandbox } from '@/lib/sandbox-api';
import { cn } from '@/lib/utils';

type TerminalEntry = {
  id: number;
  command: string;
  status: 'running' | 'done';
  result: SandboxExecResult | null;
  error: string | null;
};

const PROMPT = '~/workspace';

export function SandboxTerminal({ sandboxId }: { sandboxId: string }) {
  const [entries, setEntries] = useState<TerminalEntry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const scroller = useRef<HTMLDivElement | null>(null);

  // 输出增长后贴底;只动 DOM 不触发状态更新(会话切换由 key 重挂载重置)。
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
      // sh -lc 让管道/重定向/引号都按命令行习惯生效。
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
      <div className="flex shrink-0 items-center justify-between px-0.5 pb-2">
        <span className="text-[11px] font-semibold text-ha-dim">
          沙箱验证终端
        </span>
        {entries.length > 0 ? (
          <button
            type="button"
            onClick={() => setEntries([])}
            className="font-mono text-[11px] text-ha-faint transition-colors hover:text-ha-text-2"
          >
            清空
          </button>
        ) : null}
      </div>

      <div
        ref={scroller}
        data-testid="sandbox-terminal-output"
        className="min-h-0 flex-1 overflow-y-auto rounded-md border border-ha-border bg-ha-code p-3 font-mono text-[12px] leading-5"
      >
        {entries.length === 0 ? (
          <div className="space-y-2 text-ha-dim">
            <p className="flex items-center gap-1.5">
              <Terminal aria-hidden="true" className="h-3.5 w-3.5" />
              这是沙箱内的验证终端
            </p>
            <p className="text-[11px] leading-5">
              命令在会话沙箱（/workspace）里执行，不是本机终端；每条命令独立运行，
              <span className="text-ha-faint">cd 不会跨命令保留</span>
              ，需要时请写全路径。
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
          {PROMPT} $
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
