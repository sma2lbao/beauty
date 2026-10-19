/**
 * 本地 Agent 循环:用 fake fetch 驱动一轮「模型 → 工具 → 模型」闭环。
 * 纯 node、零网络:不请求真实模型端点。
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { AgentEvent, CommandResult, DesktopSettings } from '@shared/contract';

import { LocalAgent } from './agent';
import { SessionStore } from './session-store';
import { readTextFile, workspaceApi } from './workspace';

let workspaceRoot: string;
let userDataDir: string;
let sessions: SessionStore;

beforeEach(async () => {
  workspaceRoot = await mkdtemp(path.join(tmpdir(), 'beauty-agent-ws-'));
  userDataDir = await mkdtemp(path.join(tmpdir(), 'beauty-agent-data-'));
  sessions = new SessionStore(path.join(userDataDir, 'sessions'));
});

afterEach(async () => {
  await rm(workspaceRoot, { recursive: true, force: true });
  await rm(userDataDir, { recursive: true, force: true });
});

/** 构造一个 SSE 响应(frames 已含空行分隔)。 */
function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function sseFrame(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function chatChunk(delta: Record<string, unknown>): string {
  return sseFrame({ choices: [{ index: 0, delta }] });
}

function settings(overrides: Partial<DesktopSettings['model']> = {}): DesktopSettings {
  return {
    mode: 'local',
    cloudConsoleUrl: 'http://127.0.0.1:5173/',
    workspaceRoot,
    model: {
      baseUrl: 'http://model.test/v1',
      model: 'test-model',
      apiKey: '',
      ...overrides,
    },
  };
}

function createAgent(options: {
  fetchImpl: typeof fetch;
  onEvent?: (event: AgentEvent) => void;
  readSettings?: () => Promise<DesktopSettings>;
}): { agent: LocalAgent; events: AgentEvent[] } {
  const events: AgentEvent[] = [];
  const agent = new LocalAgent({
    sessions,
    workspace: workspaceApi,
    runCommand: async (_command, _options): Promise<CommandResult> => ({
      command: _command,
      exitCode: 0,
      stdout: 'ok',
      stderr: '',
      durationMs: 1,
      truncated: false,
      cancelled: false,
    }),
    readSettings: options.readSettings ?? (() => Promise.resolve(settings())),
    onEvent: (event) => {
      events.push(event);
      options.onEvent?.(event);
    },
    fetch: options.fetchImpl,
  });
  return { agent, events };
}

describe('LocalAgent', () => {
  it('执行一轮工具调用后把结果回填给模型并落库', async () => {
    const requests: Array<Record<string, unknown>> = [];
    let call = 0;
    const fetchImpl = (async (_input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      requests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      call += 1;
      if (call === 1) {
        // 第一轮:模型要求列目录
        return sseResponse([
          chatChunk({
            tool_calls: [
              {
                index: 0,
                id: 'call_1',
                type: 'function',
                function: { name: 'list_files', arguments: '{"path":' },
              },
            ],
          }),
          chatChunk({
            tool_calls: [{ index: 0, function: { arguments: '""}' } }],
          }),
          'data: [DONE]\n\n',
        ]);
      }
      // 第二轮:模型给出最终回答
      return sseResponse([
        chatChunk({ content: '工作区是空的。' }),
        'data: [DONE]\n\n',
      ]);
    }) as typeof fetch;

    const session = await sessions.create({ workspaceRoot });
    const { agent, events } = createAgent({ fetchImpl });

    await agent.send({ sessionId: session.id, text: '看看工作区有什么' });
    await agent.waitForIdle(session.id);

    expect(requests).toHaveLength(2);
    expect(requests[0]?.model).toBe('test-model');
    expect(JSON.stringify(requests[0]?.tools)).toContain('list_files');

    // 第二次请求里带着工具执行结果
    const secondMessages = JSON.stringify(requests[1]?.messages);
    expect(secondMessages).toContain('"role":"tool"');
    expect(secondMessages).toContain('list_files');

    const stored = await sessions.get(session.id);
    expect(stored).not.toBeNull();
    const roles = stored?.messages.map((message) => message.role);
    expect(roles).toEqual(['user', 'tool', 'assistant']);
    expect(stored?.messages[1]).toMatchObject({ toolName: 'list_files', toolStatus: 'ok' });
    expect(stored?.messages[2]?.content).toBe('工作区是空的。');

    // 事件:开始 → 增量 → 结束(ok)
    expect(events.some((event) => event.type === 'run.started')).toBe(true);
    expect(
      events.some((event) => event.type === 'assistant.delta' && event.delta.includes('工作区')),
    ).toBe(true);
    expect(events.at(-1)).toMatchObject({ type: 'run.finished', status: 'ok' });
  });

  it('write_file 工具真正写进工作区', async () => {
    let call = 0;
    const fetchImpl = (async () => {
      call += 1;
      if (call === 1) {
        return sseResponse([
          chatChunk({
            tool_calls: [
              {
                index: 0,
                id: 'call_write',
                type: 'function',
                function: {
                  name: 'write_file',
                  arguments: '{"path":"notes/hello.md","content":"# 你好"}',
                },
              },
            ],
          }),
          'data: [DONE]\n\n',
        ]);
      }
      return sseResponse([chatChunk({ content: '已写入。' }), 'data: [DONE]\n\n']);
    }) as typeof fetch;

    const session = await sessions.create({ workspaceRoot });
    const { agent } = createAgent({ fetchImpl });

    await agent.send({ sessionId: session.id, text: '写个说明文件' });
    await agent.waitForIdle(session.id);

    const written = await readTextFile(workspaceRoot, 'notes/hello.md');
    expect(written.content).toBe('# 你好');
  });

  it('未配置模型地址时以可读错误结束', async () => {
    const session = await sessions.create({ workspaceRoot });
    const { agent, events } = createAgent({
      fetchImpl: (async () => {
        throw new Error('不应该真的发请求');
      }) as typeof fetch,
      readSettings: () => Promise.resolve(settings({ baseUrl: '' })),
    });

    await agent.send({ sessionId: session.id, text: '你好' });
    await agent.waitForIdle(session.id);

    const finished = events.at(-1);
    expect(finished).toMatchObject({ type: 'run.finished', status: 'error' });
    expect(finished && 'error' in finished ? finished.error : '').toContain('baseUrl');
  });

  it('HTTP 非 2xx 时给出状态码与响应片段', async () => {
    const session = await sessions.create({ workspaceRoot });
    const { agent, events } = createAgent({
      fetchImpl: (async () =>
        new Response('model not found', { status: 404, statusText: 'Not Found' })) as typeof fetch,
    });

    await agent.send({ sessionId: session.id, text: '你好' });
    await agent.waitForIdle(session.id);

    const finished = events.at(-1);
    const message = finished && 'error' in finished ? finished.error : '';
    expect(message).toContain('404');
    expect(message).toContain('model not found');
  });

  it('cancel 会中止当前一轮并标记 cancelled', async () => {
    const session = await sessions.create({ workspaceRoot });
    const { agent, events } = createAgent({
      fetchImpl: ((_input: Parameters<typeof fetch>[0], init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new Error('aborted'));
          });
        })) as typeof fetch,
    });

    await agent.send({ sessionId: session.id, text: '跑一个长任务' });
    await agent.cancel(session.id);
    await agent.waitForIdle(session.id);

    expect(events.at(-1)).toMatchObject({ type: 'run.finished', status: 'cancelled' });
  });

  it('同一会话执行期间再次 send 会被拒绝', async () => {
    const session = await sessions.create({ workspaceRoot });
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { agent } = createAgent({
      fetchImpl: (async () => {
        await gate;
        return sseResponse([chatChunk({ content: '好' }), 'data: [DONE]\n\n']);
      }) as typeof fetch,
    });

    await agent.send({ sessionId: session.id, text: '第一条' });
    await expect(agent.send({ sessionId: session.id, text: '第二条' })).rejects.toThrow(
      '正在执行',
    );
    release?.();
    await agent.waitForIdle(session.id);
  });

  it('会话缺少工作区时报可读错误', async () => {
    const session = await sessions.create({ workspaceRoot: null });
    const { agent, events } = createAgent({
      fetchImpl: (async () => {
        throw new Error('不应该真的发请求');
      }) as typeof fetch,
      readSettings: () => Promise.resolve({ ...settings(), workspaceRoot: null }),
    });

    await agent.send({ sessionId: session.id, text: '你好' });
    await agent.waitForIdle(session.id);

    const finished = events.at(-1);
    expect(finished).toMatchObject({ type: 'run.finished', status: 'error' });
    expect(finished && 'error' in finished ? finished.error : '').toContain('工作区');
  });
});
