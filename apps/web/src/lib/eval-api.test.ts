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

  it('摘要、分页与单用例查询按 id/套件编码', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ id: 'run/1', created_at: '' }))
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, limit: 50, offset: 50 }),
      )
      .mockResolvedValueOnce(jsonResponse({ id: 'chat/a' }));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.summary('run/1');
    await evalApi.cases('run/1', 'agent core', {
      status: 'failed',
      limit: 50,
      offset: 50,
    });
    await evalApi.case('run/1', 'agent core', 'chat/a');

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/eval/runs/run%2F1/summary',
    );
    expect((fetchMock.mock.calls[1] as unknown as [string])[0]).toBe(
      '/api/eval/runs/run%2F1/suites/agent%20core/cases?limit=50&offset=50&status=failed',
    );
    expect((fetchMock.mock.calls[2] as unknown as [string])[0]).toBe(
      '/api/eval/runs/run%2F1/case?suite=agent+core&case_id=chat%2Fa',
    );
  });

  it('分页服务不可用时回退静态快照并本地切片', async () => {
    const report = {
      version: 1,
      generated_at: '',
      totals: {},
      suites: [
        {
          name: 'agent-core',
          cases: [
            { id: 'chat/a', ok: true },
            { id: 'chat/b', ok: false },
          ],
        },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse(report));
    vi.stubGlobal('fetch', fetchMock);

    const page = await evalApi.cases('offline-run', 'agent-core', {
      status: 'failed',
      limit: 50,
      offset: 0,
    });

    expect((fetchMock.mock.calls[2] as unknown as [string])[0]).toBe(
      '/eval/runs/offline-run.json',
    );
    expect(page.total).toBe(1);
    expect(page.items[0]).toMatchObject({ id: 'chat/b', suite: 'agent-core' });
  });

  it('对比请求编码 base 与 candidate', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.compare('base-1', 'cand/2');

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/eval/compare?base=base-1&candidate=cand%2F2',
    );
  });

  it('数据集与基线走 eval-service,数据集名按路径编码', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await evalApi.datasets();
    await evalApi.dataset('agent core');
    await evalApi.baselines();

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      '/api/eval/datasets',
    );
    expect((fetchMock.mock.calls[1] as unknown as [string])[0]).toBe(
      '/api/eval/datasets/agent%20core',
    );
    expect((fetchMock.mock.calls[2] as unknown as [string])[0]).toBe(
      '/api/eval/baselines',
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
