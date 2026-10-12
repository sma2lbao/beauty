import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/runs-api';

import { knowledgeApi } from './knowledge-api';

function mockFetch(
  status: number,
  body?: unknown,
): ReturnType<typeof vi.fn> {
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

describe('knowledgeApi', () => {
  it('列表走 /knowledge-api 前缀并带鉴权头', async () => {
    const fetchMock = mockFetch(200, []);

    await knowledgeApi.documents();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/knowledge-api/v1/knowledge/documents');
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.stringify(init.headers)).toContain('X-Tenant-Id');
  });

  it('入库发送标题/正文/来源', async () => {
    const document = {
      id: 'd1',
      title: '合同备忘',
      source: 'manual',
      size_bytes: 10,
      chunk_count: 1,
      embedding_model: 'hashing-256',
      created_at: '2026-10-13T00:00:00+00:00',
    };
    const fetchMock = mockFetch(201, document);

    await expect(
      knowledgeApi.ingest({ title: '合同备忘', content: '正文', source: 'manual' }),
    ).resolves.toEqual(document);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/knowledge-api/v1/knowledge/documents');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      title: '合同备忘',
      content: '正文',
      source: 'manual',
    });
  });

  it('检索发送 query 与 limit', async () => {
    const fetchMock = mockFetch(200, {
      query: '合同',
      provider: 'local',
      model: 'hashing-256',
      hits: [],
    });

    await knowledgeApi.search('合同', 3);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/knowledge-api/v1/knowledge/search');
    expect(JSON.parse(init.body as string)).toEqual({ query: '合同', limit: 3 });
  });

  it('删除 204 无正文也解析为 undefined', async () => {
    mockFetch(204);

    await expect(knowledgeApi.remove('d1')).resolves.toBeUndefined();
  });

  it('错误体映射为 ApiError(统一契约)', async () => {
    mockFetch(404, {
      code: 'knowledge.document_not_found',
      message: '文档不存在',
    });

    const error = await knowledgeApi.remove('nope').catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).code).toBe('knowledge.document_not_found');
  });

  it('非 JSON 错误体保留兜底文案', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('not json');
        },
      })),
    );

    const error = (await knowledgeApi
      .documents()
      .catch((err: unknown) => err)) as ApiError;
    expect(error.code).toBe('request_failed');
    expect(error.status).toBe(500);
  });
});
