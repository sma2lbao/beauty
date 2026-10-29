/**
 * 沙箱终端契约:持久 shell 会话(SSE)为主,开会话失败才降级单命令。
 *
 * 覆盖:开会话 + output 帧渲染、输入写 stdin、Ctrl-C、exit 帧、降级路径、
 * 断线后带 Last-Event-ID 重连;外加两个纯函数(清洗 ANSI、解析帧)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SseFrame } from '@/lib/sse';

import {
  parseSessionEvent,
  SandboxTerminal,
  stripTerminalControls,
} from './sandbox-terminal';

const encoder = new TextEncoder();

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** 一个把给定帧一次性写完后关闭的假 SSE 响应。 */
function sseResponse(frames: string[]): Response {
  return {
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frames.join('')));
        controller.close();
      },
    }),
  } as unknown as Response;
}

/** 拼一个带 id 行的 SSE 帧。 */
function frame(event: string, payload: unknown, id = 1): string {
  return (
    'id: ' +
    String(id) +
    '\nevent: ' +
    event +
    '\ndata: ' +
    JSON.stringify(payload) +
    '\n\n'
  );
}

type Call = { url: string; init: RequestInit };

type Handler = (
  url: string,
  init: RequestInit,
  streamIndex: number,
) => Response | Promise<Response>;

/** 按 URL 分派的 fetch 桩:记下全部调用,流的第 n 次单独计数。 */
function installFetch(handler: Handler) {
  const calls: Call[] = [];
  let streamCount = 0;
  const mock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init: init ?? {} });
    const isStream = url.endsWith('/stream');
    const index = isStream ? streamCount : 0;
    if (isStream) streamCount += 1;
    return handler(url, init ?? {}, index);
  });
  vi.stubGlobal('fetch', mock);
  return { calls, mock };
}

const SESSION = {
  session_id: 'ss-1',
  sandbox_id: 'sb-1',
  cwd: '/workspace',
  created_at: '2026-10-01T00:00:00Z',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('stripTerminalControls', () => {
  it('剥掉 CSI/OSC/单字符转义与回车,保留正文', () => {
    expect(
      stripTerminalControls('a\u001b[1mb\u001b]0;title\u0007c\r\nd\u001bXe'),
    ).toBe('abc\nde');
  });

  it('中文与换行原样保留', () => {
    expect(stripTerminalControls('你好\n世界\n')).toBe('你好\n世界\n');
  });
});

describe('parseSessionEvent', () => {
  function makeFrame(event: string, data: unknown, id = 1): SseFrame {
    const text = frame(event, data, id);
    return { event, data: JSON.stringify(data), raw: text };
  }

  it('output 帧带 seq 与文本', () => {
    expect(parseSessionEvent(makeFrame('output', { seq: 3, data: 'hi' }, 3))).toEqual(
      { seq: 3, kind: 'output', data: 'hi' },
    );
  });

  it('payload 没有 seq 时用 id 行兜底', () => {
    expect(parseSessionEvent(makeFrame('output', { data: 'x' }, 9))).toEqual({
      seq: 9,
      kind: 'output',
      data: 'x',
    });
  });

  it('exit / error 帧', () => {
    expect(parseSessionEvent(makeFrame('exit', { seq: 2, exit_code: 0 }))).toEqual(
      { seq: 2, kind: 'exit', exitCode: 0 },
    );
    expect(
      parseSessionEvent(
        makeFrame('error', { seq: 1, code: 'sandbox.error', message: '炸了' }),
      ),
    ).toEqual({ seq: 1, kind: 'error', code: 'sandbox.error', message: '炸了' });
  });

  it('坏 JSON / 未知事件 / 无游标都返回 null', () => {
    expect(
      parseSessionEvent({ event: 'output', data: '{oops', raw: 'data: {oops' }),
    ).toBeNull();
    expect(parseSessionEvent(makeFrame('ping', { seq: 1 }))).toBeNull();
    expect(
      parseSessionEvent({ event: 'output', data: '{"data":"x"}', raw: 'data: {}' }),
    ).toBeNull();
  });
});

describe('SandboxTerminal', () => {
  it('开会话后流式渲染 output,并清掉 ANSI 控制序列', async () => {
    const { calls } = installFetch((url) => {
      if (url.endsWith('/sessions')) return jsonResponse(SESSION, 201);
      if (url.endsWith('/stream')) {
        return sseResponse([
          frame('output', { seq: 1, data: '\u001b[32mok\u001b[0m\n' }),
        ]);
      }
      return jsonResponse({ code: 'not_found' }, 404);
    });

    render(<SandboxTerminal sandboxId="sb-1" />);

    await waitFor(() =>
      expect(
        screen.getByTestId('sandbox-terminal-output').textContent,
      ).toContain('ok\n'),
    );
    const open = calls.find((call) => call.url.endsWith('/sessions'));
    expect(open?.url).toBe('/workspace-api/v1/sandboxes/sb-1/sessions');
    expect(JSON.parse(String(open?.init.body))).toEqual({ cols: 120, rows: 32 });
    const stream = calls.find((call) => call.url.endsWith('/stream'));
    expect(stream?.url).toBe(
      '/workspace-api/v1/sandboxes/sb-1/sessions/ss-1/stream',
    );
    expect(
      (stream?.init.headers as Record<string, string>).Accept,
    ).toBe('text/event-stream');
  });

  it('回车把输入原样写 stdin,Ctrl-C 写 \x03', async () => {
    const { calls } = installFetch((url) => {
      if (url.endsWith('/sessions')) return jsonResponse(SESSION, 201);
      if (url.endsWith('/stream')) {
        return sseResponse([frame('output', { seq: 1, data: '$ ' })]);
      }
      return jsonResponse({}, 204);
    });

    render(<SandboxTerminal sandboxId="sb-1" />);
    await waitFor(() =>
      expect(screen.getByTestId('sandbox-terminal-output').textContent).toContain(
        '$',
      ),
    );

    const input = screen.getByLabelText('终端命令');
    fireEvent.change(input, { target: { value: 'ls -la' } });
    const form = input.closest('form');
    if (!form) throw new Error('终端输入框不在 form 里');
    fireEvent.submit(form);

    await waitFor(() =>
      expect(
        calls.filter((call) => call.url.endsWith('/input')).length,
      ).toBeGreaterThan(0),
    );
    const sent = calls.find((call) => call.url.endsWith('/input'));
    expect(sent?.url).toBe(
      '/workspace-api/v1/sandboxes/sb-1/sessions/ss-1/input',
    );
    expect(JSON.parse(String(sent?.init.body))).toEqual({ data: 'ls -la\n' });

    fireEvent.click(screen.getByRole('button', { name: 'Ctrl-C' }));
    await waitFor(() => {
      const bodies = calls
        .filter((call) => call.url.endsWith('/input'))
        .map((call) => JSON.parse(String(call.init.body)) as { data: string });
      expect(bodies.some((body) => body.data === '\u0003')).toBe(true);
    });
  });

  it('exit 帧后显示退出码并停手', async () => {
    installFetch((url) => {
      if (url.endsWith('/sessions')) return jsonResponse(SESSION, 201);
      if (url.endsWith('/stream')) {
        return sseResponse([
          frame('output', { seq: 1, data: 'done\n' }, 1),
          frame('exit', { seq: 2, exit_code: 7 }, 2),
        ]);
      }
      return jsonResponse({}, 204);
    });

    render(<SandboxTerminal sandboxId="sb-1" />);

    const exit = await screen.findByTestId('sandbox-terminal-exit');
    expect(exit.textContent).toContain('退出码 7');
    expect(screen.getByTestId('sandbox-terminal-status').textContent).toContain(
      '已退出',
    );
    // 退出后输入框禁用(不再往后写 stdin)
    expect((screen.getByLabelText('终端命令') as HTMLInputElement).disabled).toBe(
      true,
    );
  });

  it('开会话失败时降级为单命令模式,仍能执行命令', async () => {
    const { calls } = installFetch((url) => {
      if (url.endsWith('/sessions')) {
        return jsonResponse(
          { code: 'sandbox.provider_unavailable', message: 'pty 不可用' },
          503,
        );
      }
      if (url.endsWith('/exec')) {
        return jsonResponse({
          exit_code: 0,
          stdout: 'hi',
          stderr: '',
          duration_ms: 5,
          truncated: false,
        });
      }
      return jsonResponse({}, 204);
    });

    render(<SandboxTerminal sandboxId="sb-1" />);

    expect(
      await screen.findByText('会话终端不可用，已降级为单命令模式'),
    ).toBeTruthy();
    expect(screen.getByText(/pty 不可用/)).toBeTruthy();

    const input = screen.getByLabelText('终端命令');
    fireEvent.change(input, { target: { value: 'echo hi' } });
    const form = input.closest('form');
    if (!form) throw new Error('终端输入框不在 form 里');
    fireEvent.submit(form);

    await waitFor(() =>
      expect(
        screen.getByTestId('sandbox-terminal-output').textContent,
      ).toContain('hi'),
    );
    expect(screen.getByText('退出码 0')).toBeTruthy();
    const exec = calls.find((call) => call.url.endsWith('/exec'));
    expect(JSON.parse(String(exec?.init.body))).toMatchObject({
      argv: ['sh', '-lc', 'echo hi'],
      cwd: '/workspace',
    });
  });

  it('断线后带 Last-Event-ID 续传(不丢游标)', async () => {
    const { calls } = installFetch((url, _init, index) => {
      if (url.endsWith('/sessions')) return jsonResponse(SESSION, 201);
      if (url.endsWith('/stream')) {
        if (index === 0) {
          return sseResponse([frame('output', { seq: 1, data: 'one\n' }, 1)]);
        }
        if (index === 1) {
          return sseResponse([frame('output', { seq: 2, data: 'two\n' }, 2)]);
        }
        return sseResponse([frame('exit', { seq: 3, exit_code: 0 }, 3)]);
      }
      return jsonResponse({}, 204);
    });

    render(<SandboxTerminal sandboxId="sb-1" />);

    await waitFor(
      () =>
        expect(
          screen.getByTestId('sandbox-terminal-output').textContent,
        ).toContain('two'),
      { timeout: 3000 },
    );

    const streams = calls.filter((call) => call.url.endsWith('/stream'));
    const cursor = (call: Call | undefined) =>
      (call?.init.headers as Record<string, string> | undefined)?.[
        'Last-Event-ID'
      ];
    expect(streams.length).toBeGreaterThanOrEqual(2);
    expect(cursor(streams[1])).toBe('1');
  });
});
