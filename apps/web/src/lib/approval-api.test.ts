import { afterEach, describe, expect, it, vi } from 'vitest';

import { approvalApi } from './approval-api';

function mockFetch(status: number, body?: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('approvalApi', () => {
  it('list 读 GET /api/v1/approval-policies 并为旧数据补空参数列', async () => {
    const fetchMock = mockFetch(200, [{ tool: 'exec', action: 'confirm' }]);

    const policies = await approvalApi.list();

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/approval-policies');
    expect(policies).toEqual([
      {
        tool: 'exec',
        action: 'confirm',
        arg_name: '',
        arg_match: '',
        arg_value: '',
      },
    ]);
  });

  it('replace 把整表 PUT 到同一路径', async () => {
    const fetchMock = mockFetch(200, [
      { tool: 'exec', action: 'auto', arg_name: '', arg_match: '', arg_value: '' },
    ]);

    const policies = await approvalApi.replace([
      { tool: 'exec', action: 'auto' },
    ]);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/approval-policies');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({
      policies: [{ tool: 'exec', action: 'auto' }],
    });
    expect(policies[0]?.action).toBe('auto');
  });

  it('参数级规则的三列原样保留', async () => {
    mockFetch(200, [
      {
        tool: '*',
        action: 'confirm',
        arg_name: 'command',
        arg_match: 'prefix',
        arg_value: 'rm -rf',
      },
    ]);

    const policies = await approvalApi.list();

    expect(policies[0]).toEqual({
      tool: '*',
      action: 'confirm',
      arg_name: 'command',
      arg_match: 'prefix',
      arg_value: 'rm -rf',
    });
  });
});
