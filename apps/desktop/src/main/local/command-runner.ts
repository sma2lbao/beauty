/**
 * 本地命令执行器:在工作区目录里跑一条 shell 命令。
 *
 * 约束(MVP 的「安全网」):
 * - darwin/linux 走 `/bin/sh -c`,win32 走 `cmd.exe /d /s /c`;
 * - 默认 120s 超时,超时强杀;
 * - stdout/stderr 各自最多保留 256KB,超出部分截断并标记;
 * - 支持 AbortSignal 与 handle.kill() 两种取消方式,会话取消会沿 signal 传进来。
 *
 * 纯 Node 逻辑,不依赖 electron;spawn 可注入,便于测试。
 */
import { spawn as nodeSpawn } from 'node:child_process';

import type { CommandResult } from '@shared/contract';

/** 默认命令超时(毫秒)。 */
export const DEFAULT_COMMAND_TIMEOUT_MS = 120_000;

/** stdout / stderr 各自默认的保留上限(字节)。 */
export const DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024;

/** 超时退出码,沿用 coreutils `timeout` 的约定。 */
const EXIT_CODE_TIMEOUT = 124;

/** 主动取消退出码,沿用 128 + SIGINT(2) 的约定。 */
const EXIT_CODE_CANCELLED = 130;

/** 子进程根本起不来(例如 shell 缺失)时的退出码。 */
const EXIT_CODE_SPAWN_FAILED = 127;

export interface RunCommandOptions {
  /** 命令的工作目录(通常是工作区根目录)。 */
  cwd: string;
  timeoutMs?: number;
  maxOutputBytes?: number;
  /** 会话级取消信号。 */
  signal?: AbortSignal;
  env?: NodeJS.ProcessEnv;
  /** 默认取 process.platform;测试可覆盖。 */
  platform?: NodeJS.Platform;
  /** 默认取 node:child_process.spawn;测试可注入。 */
  spawn?: typeof nodeSpawn;
}

/** 命令句柄:promise 在进程结束后 resolve,必要时可主动 kill。 */
export interface CommandHandle {
  promise: Promise<CommandResult>;
  kill(): void;
}

interface ShellInvocation {
  file: string;
  args: string[];
  /** Windows 下 cmd.exe 需要原样传参,避免 Node 二次加引号。 */
  windowsVerbatimArguments: boolean;
}

/** 按平台选择 shell 调用方式。 */
export function resolveShellInvocation(command: string, platform: NodeJS.Platform): ShellInvocation {
  if (platform === 'win32') {
    return {
      file: 'cmd.exe',
      args: ['/d', '/s', '/c', command],
      windowsVerbatimArguments: true,
    };
  }
  return { file: '/bin/sh', args: ['-c', command], windowsVerbatimArguments: false };
}

/** 有上限的输出收集器:超过 limit 的字节直接丢弃并标记 truncated。 */
class OutputCollector {
  private readonly chunks: Buffer[] = [];
  private bytes = 0;
  truncated = false;

  constructor(private readonly limit: number) {}

  push(chunk: Buffer): void {
    if (this.bytes >= this.limit) {
      if (chunk.length > 0) this.truncated = true;
      return;
    }
    const remaining = this.limit - this.bytes;
    if (chunk.length > remaining) {
      this.chunks.push(chunk.subarray(0, remaining));
      this.bytes = this.limit;
      this.truncated = true;
      return;
    }
    this.chunks.push(chunk);
    this.bytes += chunk.length;
  }

  appendNote(text: string): void {
    this.push(Buffer.from(text, 'utf8'));
  }

  toString(): string {
    return Buffer.concat(this.chunks).toString('utf8');
  }
}

/**
 * 执行一条命令。返回句柄而非直接 Promise,这样调用方可以先拿到 kill。
 */
export function runCommand(command: string, options: RunCommandOptions): CommandHandle {
  const trimmed = typeof command === 'string' ? command.trim() : '';
  if (!trimmed) {
    return { promise: Promise.reject(new Error('命令不能为空。')), kill: () => undefined };
  }

  const startedAt = Date.now();
  const timeoutMs = options.timeoutMs ?? DEFAULT_COMMAND_TIMEOUT_MS;
  const maxOutputBytes = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
  const platform = options.platform ?? process.platform;
  const spawnImpl = options.spawn ?? nodeSpawn;
  const stdout = new OutputCollector(maxOutputBytes);
  const stderr = new OutputCollector(maxOutputBytes);

  let childAlive = false;
  let settled = false;
  let cancelled = false;
  let timedOut = false;
  let spawnFailed = false;
  let timer: NodeJS.Timeout | undefined;
  let externalAbortListener: (() => void) | undefined;
  let killImpl: () => void = () => undefined;

  const buildResult = (exitCode: number, extraStderr = ''): CommandResult => {
    if (extraStderr) stderr.appendNote(extraStderr);
    return {
      command: trimmed,
      exitCode,
      stdout: stdout.toString(),
      stderr: stderr.toString(),
      durationMs: Date.now() - startedAt,
      truncated: stdout.truncated || stderr.truncated,
      cancelled,
    };
  };

  const promise = new Promise<CommandResult>((resolve) => {
    if (options.signal?.aborted) {
      cancelled = true;
      settled = true;
      resolve(buildResult(EXIT_CODE_CANCELLED, '\n[命令已被取消]'));
      return;
    }

    const invocation = resolveShellInvocation(trimmed, platform);
    let child: ReturnType<typeof nodeSpawn>;
    try {
      child = spawnImpl(invocation.file, invocation.args, {
        cwd: options.cwd,
        env: { ...process.env, ...options.env },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        windowsVerbatimArguments: invocation.windowsVerbatimArguments,
      });
    } catch (error) {
      settled = true;
      spawnFailed = true;
      stderr.appendNote(`无法启动命令:${(error as Error).message}`);
      resolve(buildResult(EXIT_CODE_SPAWN_FAILED));
      return;
    }
    childAlive = true;

    const finish = (exitCode: number, note = ''): void => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (externalAbortListener && options.signal) {
        options.signal.removeEventListener('abort', externalAbortListener);
      }
      resolve(buildResult(exitCode, note));
    };

    const terminate = (): void => {
      if (!childAlive || settled) return;
      try {
        // SIGKILL 在 Windows 上等价于 TerminateProcess;这里不追求优雅退出。
        child.kill('SIGKILL');
      } catch {
        // 进程可能已经退出,忽略即可。
      }
    };

    timer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      cancelled = true;
      terminate();
    }, timeoutMs);
    // 命令结束后不让定时器拖住事件循环。
    timer.unref?.();

    externalAbortListener = (): void => {
      if (settled) return;
      cancelled = true;
      terminate();
    };
    options.signal?.addEventListener('abort', externalAbortListener, { once: true });

    child.stdout?.on('data', (chunk: Buffer | string) => {
      stdout.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk);
    });
    child.stderr?.on('data', (chunk: Buffer | string) => {
      stderr.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk);
    });
    child.on('error', (error) => {
      childAlive = false;
      spawnFailed = true;
      finish(EXIT_CODE_SPAWN_FAILED, `无法启动命令:${error.message}`);
    });
    child.on('close', (code, signal) => {
      childAlive = false;
      if (settled) return;
      if (timedOut) {
        finish(EXIT_CODE_TIMEOUT, `\n[命令执行超时(${timeoutMs}ms),已被终止]`);
        return;
      }
      if (cancelled) {
        finish(EXIT_CODE_CANCELLED, '\n[命令已被取消]');
        return;
      }
      if (spawnFailed) {
        finish(EXIT_CODE_SPAWN_FAILED);
        return;
      }
      // 被信号杀掉且非我们主动取消时,用非零码告诉界面"没正常结束"。
      const exitCode = typeof code === 'number' ? code : signal ? 1 : 0;
      finish(exitCode);
    });

    killImpl = (): void => {
      if (settled) return;
      cancelled = true;
      terminate();
    };
  });

  return {
    promise,
    kill: () => killImpl(),
  };
}
