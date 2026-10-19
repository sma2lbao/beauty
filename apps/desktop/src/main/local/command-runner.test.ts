/**
 * 命令执行器:退出码、stderr、超时、取消、输出截断。
 * 只跑极短的本地 shell 命令,零网络。
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DEFAULT_COMMAND_TIMEOUT_MS, resolveShellInvocation, runCommand } from './command-runner';

let cwd: string;

beforeEach(async () => {
  cwd = await mkdtemp(path.join(tmpdir(), 'beauty-command-'));
});

afterEach(async () => {
  await rm(cwd, { recursive: true, force: true });
});

describe('resolveShellInvocation', () => {
  it('darwin/linux 走 /bin/sh -c', () => {
    expect(resolveShellInvocation('echo hi', 'linux')).toMatchObject({
      file: '/bin/sh',
      args: ['-c', 'echo hi'],
    });
    expect(resolveShellInvocation('echo hi', 'darwin').file).toBe('/bin/sh');
  });

  it('win32 走 cmd.exe /d /s /c', () => {
    const invocation = resolveShellInvocation('dir', 'win32');
    expect(invocation.file).toMatch(/cmd\.exe$/i);
    expect(invocation.args).toEqual(['/d', '/s', '/c', 'dir']);
  });
});

describe('runCommand', () => {
  it('返回 stdout 与退出码', async () => {
    const result = await runCommand('printf "hello"', { cwd }).promise;
    expect(result.stdout).toBe('hello');
    expect(result.exitCode).toBe(0);
    expect(result.cancelled).toBe(false);
    expect(result.truncated).toBe(false);
  });

  it('保留 stderr 与非零退出码', async () => {
    const result = await runCommand('printf "boom" >&2; exit 3', { cwd }).promise;
    expect(result.stderr).toContain('boom');
    expect(result.exitCode).toBe(3);
  });

  it('超时后强杀并给出 124', async () => {
    const result = await runCommand('sleep 5', { cwd, timeoutMs: 100 }).promise;
    expect(result.exitCode).toBe(124);
    expect(result.stderr).toContain('超时');
    expect(result.durationMs).toBeLessThan(4000);
  });

  it('AbortSignal 取消后标记 cancelled', async () => {
    const controller = new AbortController();
    const handle = runCommand('sleep 5', { cwd, signal: controller.signal });
    setTimeout(() => controller.abort(), 50);

    const result = await handle.promise;
    expect(result.cancelled).toBe(true);
    expect(result.exitCode).not.toBe(0);
  });

  it('handle.kill() 可主动结束进程', async () => {
    const handle = runCommand('sleep 5', { cwd });
    setTimeout(() => handle.kill(), 50);
    const result = await handle.promise;
    expect(result.durationMs).toBeLessThan(4000);
  });

  it('输出超过上限时截断并标记', async () => {
    const result = await runCommand('printf "0123456789"', { cwd, maxOutputBytes: 4 }).promise;
    expect(result.truncated).toBe(true);
    expect(result.stdout.length).toBeLessThanOrEqual(4);
  });

  it('空命令直接报错', async () => {
    await expect(runCommand('   ', { cwd }).promise).rejects.toThrow('命令不能为空');
  });

  it('默认超时是 120s', () => {
    expect(DEFAULT_COMMAND_TIMEOUT_MS).toBe(120_000);
  });
});
