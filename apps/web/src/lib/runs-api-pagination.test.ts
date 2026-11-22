import { afterEach, describe, expect, it, vi } from 'vitest';

import { runsApi } from '@/lib/runs-api';

describe('runsApi.list 分页与搜索', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('分页/搜索参数拼进查询串,旧数组响应归一化', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{ id: 'r1', status: 'pending' }],
        next_cursor: 'abc',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const page = await runsApi.list({ limit: 20, q: '  周报 ', cursor: 'x' });
    expect(page.next_cursor).toBe('abc');
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/runs?limit=20&q=%E5%91%A8%E6%8A%A5&cursor=x');

    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'r2' }],
    });
    const legacy = await runsApi.list();
    expect(legacy).toEqual({ items: [{ id: 'r2' }], next_cursor: null });
  });
});
