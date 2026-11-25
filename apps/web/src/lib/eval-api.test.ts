import { afterEach, describe, expect, it, vi } from 'vitest';

import { evalApi } from './eval-api';

const INDEX = { version: 1, generated_at: '', runs: [] };

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('evalApi', () => {
  it('优先读同源 /api/eval,运行详情按 id 编码', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(INDEX));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.index();
    await evalApi.run('run 1');
    await evalApi.run('run/2');

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/eval/runs?limit=5',
    );
    expect((fetchMock.mock.calls[1] as unknown as [string])[0]).toBe(
      '/api/eval/runs/run%201',
    );
    expect((fetchMock.mock.calls[2] as unknown as [string])[0]).toBe(
      '/api/eval/runs/run%2F2',
    );
  });

  it('对比请求编码 base 与 candidate', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.compare('base-1', 'cand/2');

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/eval/compare?base=base-1&candidate=cand%2F2',
    );
  });

  it('服务不可用时回退静态快照', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse(INDEX))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse({ ...INDEX, suites: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.index();
    await evalApi.run('run 1');

    expect((fetchMock.mock.calls[1] as unknown as [string])[0]).toBe(
      '/eval/index.json',
    );
    expect((fetchMock.mock.calls[3] as unknown as [string])[0]).toBe(
      '/eval/runs/run%201.json',
    );
  });
});
