import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RunEvent } from '@/lib/runs-api';
import {
  ApiError,
  mergeEvents,
  parseEventFrame,
  runsApi,
  streamRunEvents,
} from '@/lib/runs-api';
import { setStoredToken } from '@/lib/auth';

const event: RunEvent = {
  seq: 3,
  type: 'run.planned.v1',
  payload: { attempt: 1 },
};

describe('parseEventFrame', () => {
  it('解析 data 行里的 RunEventRead', () => {
    const frame = [
      'id: 3',
      'event: run.planned.v1',
      `data: ${JSON.stringify({ ...event, run_id: 'r1', id: 'abc' })}`,
    ].join('\n');

    expect(parseEventFrame(frame)).toEqual({
      seq: 3,
      type: 'run.planned.v1',
      payload: { attempt: 1 },
      occurred_at: undefined,
    });
  });

  it('stream.end 帧没有 seq → null', () => {
    expect(
      parseEventFrame('event: stream.end\ndata: {"status":"completed"}'),
    ).toBeNull();
  });

  it('坏 JSON / 空 data → null(不抛)', () => {
    expect(parseEventFrame('event: x\ndata: {oops')).toBeNull();
    expect(parseEventFrame(': keep-alive')).toBeNull();
  });
});

describe('mergeEvents', () => {
  it('去重并按 seq 升序', () => {
    const merged = mergeEvents([event], {
      seq: 2,
      type: 'run.created.v1',
      payload: {},
    });
    expect(merged.map((item) => item.seq)).toEqual([2, 3]);
  });

  it('重复 seq 不重复插入(SSE 重放/续传)', () => {
    const merged = mergeEvents([event], event);
    expect(merged).toHaveLength(1);
  });
});

describe('runsApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('携带 Bearer 鉴权头并返回 JSON', async () => {
    setStoredToken('test-jwt');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'r1', status: 'pending' }],
    });
    vi.stubGlobal('fetch', fetchMock);

    const runs = await runsApi.list();

    expect(runs).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs?limit=50');
    expect((init.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer test-jwt',
    );
  });

  it('后端错误契约映射为 ApiError(code/message)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({
          code: 'run.invalid_state',
          message: 'run 状态不允许',
        }),
      }),
    );

    await expect(runsApi.approve('r1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'run.invalid_state',
      message: 'run 状态不允许',
    });
  });

  it('交付物列表走 /artifacts 端点并返回文件元数据', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: 'a1',
          run_id: 'r1',
          kind: 'markdown',
          title: '周报',
          filename: 'weekly.md',
          content_type: 'text/markdown',
          size_bytes: 12,
          storage: 'filesystem',
          source: 'report',
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
    });
    vi.stubGlobal('fetch', fetchMock);

    const artifacts = await runsApi.artifacts('r1');

    expect(artifacts[0]).toMatchObject({
      id: 'a1',
      filename: 'weekly.md',
      content_type: 'text/markdown',
    });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/artifacts');
  });

  it('运行项统一投影走 /items 端点并保留 data 原始字段', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          item_id: 'r1:plan:2',
          thread_id: 'r1',
          turn_id: 't1',
          kind: 'plan',
          phase: 'completed',
          status: 'completed',
          node: 'plan',
          step_id: 'r1:1:plan:1',
          attempt: 1,
          title: '执行计划',
          seq: 2,
          started_at: '2026-01-01T00:00:00Z',
          finished_at: '2026-01-01T00:00:01Z',
          duration_ms: 1000,
          data: { version: 2, supersedes: 'r1:plan:1', content: '计划正文' },
        },
      ],
    });
    vi.stubGlobal('fetch', fetchMock);

    const items = await runsApi.items('r1');

    expect(items[0]).toMatchObject({
      item_id: 'r1:plan:2',
      kind: 'plan',
      phase: 'completed',
    });
    expect(items[0]?.data.version).toBe(2);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/items');
  });

  it('子代理列表走 /subagents 端点并保留 usage/summary', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          run_id: 'child-1',
          title: '整理依赖清单',
          agent_name: '依赖分析',
          status: 'completed',
          status_reason: null,
          parent_run_id: 'r1',
          created_at: '2026-01-01T00:00:02Z',
          usage: {
            calls: 3,
            prompt_tokens: 120,
            completion_tokens: 80,
            total_tokens: 200,
            by_model: [
              {
                model: 'm1',
                calls: 3,
                prompt_tokens: 120,
                completion_tokens: 80,
                total_tokens: 200,
              },
            ],
          },
          summary: '完成依赖梳理',
        },
      ],
    });
    vi.stubGlobal('fetch', fetchMock);

    const subagents = await runsApi.subagents('r1');

    expect(subagents[0]).toMatchObject({
      run_id: 'child-1',
      agent_name: '依赖分析',
      status: 'completed',
      parent_run_id: 'r1',
    });
    expect(subagents[0]?.usage.total_tokens).toBe(200);
    expect(subagents[0]?.summary).toBe('完成依赖梳理');
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/subagents');
  });

  it('run 用量走 /usage 端点并保留 by_model', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: 'r1',
        calls: 2,
        prompt_tokens: 10,
        completion_tokens: 5,
        total_tokens: 15,
        by_model: [
          {
            model: 'm1',
            calls: 2,
            prompt_tokens: 10,
            completion_tokens: 5,
            total_tokens: 15,
          },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const usage = await runsApi.usage('r1');

    expect(usage).toMatchObject({ run_id: 'r1', calls: 2, total_tokens: 15 });
    expect(usage.by_model[0]?.model).toBe('m1');
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/usage');
  });

  it('下载交付物返回 Blob(走二进制端点,不解析 JSON)', async () => {
    setStoredToken('test-jwt');
    const blob = new Blob(['PNG'], { type: 'image/png' });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => blob,
    });
    vi.stubGlobal('fetch', fetchMock);

    const downloaded = await runsApi.downloadArtifact('r1', 'a1');

    expect(downloaded).toBe(blob);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/artifacts/a1/download');
    expect((init.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer test-jwt',
    );
  });

  it('回答挂起提问走 /answer 端点(POST,202 返回 accepted)', async () => {
    setStoredToken('test-jwt');
    const accepted = {
      run: { id: 'r1', status: 'running' },
      accepted: true,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => accepted,
    });
    vi.stubGlobal('fetch', fetchMock);

    const body = {
      question_id: 'q1',
      action: 'answer' as const,
      answers: [{ index: 0, labels: ['本周文档'], custom: null }],
    };
    const result = await runsApi.answerRunQuestion('r1', body);

    expect(result.accepted).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs/r1/answer');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual(body);
    expect((init.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer test-jwt',
    );
  });

  it('非 JSON 错误体回退通用文案', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error('not json');
        },
      }),
    );

    await expect(runsApi.get('r1')).rejects.toBeInstanceOf(ApiError);
  });
});

describe('streamRunEvents', () => {
  it('按帧推送事件,stream.end 时收尾', async () => {
    const encoder = new TextEncoder();
    const payload = [
      `id: 1\nevent: run.created.v1\ndata: ${JSON.stringify({ seq: 1, type: 'run.created.v1', payload: {} })}\n\n`,
      `id: 2\nevent: run.completed.v1\ndata: ${JSON.stringify({ seq: 2, type: 'run.completed.v1', payload: {} })}\n\n`,
      'event: stream.end\ndata: {"status":"completed"}\n\n',
    ].join('');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        body: new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(payload));
            controller.close();
          },
        }),
      }),
    );
    const received: number[] = [];
    const onEnd = vi.fn();
    const onOpen = vi.fn();

    await streamRunEvents('r1', {
      onEvent: (item) => received.push(item.seq),
      onOpen,
      onEnd,
    });

    expect(onOpen).toHaveBeenCalledOnce();
    expect(received).toEqual([1, 2]);
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it('重连时带 Last-Event-ID 请求头', async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('event: stream.end\ndata: {}\n\n'));
          controller.close();
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await streamRunEvents('r1', { lastEventId: 5, onEvent: () => {} });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['Last-Event-ID']).toBe('5');
  });

  it('没有 stream.end 的关闭抛 interrupted(交给上层重连)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        body: new ReadableStream({
          start(controller) {
            controller.close();
          },
        }),
      }),
    );

    await expect(
      streamRunEvents('r1', { onEvent: () => {} }),
    ).rejects.toMatchObject({
      code: 'stream.interrupted',
    });
  });
});
