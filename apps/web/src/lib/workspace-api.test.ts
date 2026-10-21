import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './http';
import { workspaceApi } from './workspace-api';
import { setStoredToken } from './auth';

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
  window.localStorage.clear();
});

describe('workspaceApi', () => {
  it('工作区列表走 /workspace-api 前缀并带 Bearer 鉴权头', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, []);

    await workspaceApi.workspaces();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/workspace-api/v1/workspaces');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-jwt',
    });
  });

  it('默认工作区走 /workspaces/default(幂等建档)', async () => {
    const fetchMock = mockFetch(200, { id: 'ws-1', name: '默认工作区' });

    await workspaceApi.defaultWorkspace();

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/workspaces/default',
    );
  });

  it('交付物列表按工作区取', async () => {
    const fetchMock = mockFetch(200, []);

    await workspaceApi.artifacts('ws-1');

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/workspaces/ws-1/artifacts',
    );
  });

  it('创建交付物之外的删除走 DELETE 且 204 无正文', async () => {
    const fetchMock = mockFetch(204);

    await expect(workspaceApi.removeArtifact('a1')).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/workspace-api/v1/artifacts/a1');
    expect(init.method).toBe('DELETE');
  });

  it('下载产物走二进制端点并返回 Blob', async () => {
    const blob = new Blob(['hello'], { type: 'text/markdown' });
    const fetchMock = vi.fn(async (..._args: unknown[]) => ({
      ok: true,
      status: 200,
      blob: async () => blob,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const downloaded = await workspaceApi.downloadArtifact('a1');

    expect(downloaded).toBe(blob);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/artifacts/a1/download',
    );
  });

  it('对象丢失映射为 500 的 ApiError(内部存储问题)', async () => {
    mockFetch(500, {
      code: 'artifact.object_missing',
      message: '交付物对象不存在',
    });

    const error = (await workspaceApi
      .artifact('a1')
      .catch((err: unknown) => err)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(500);
    expect(error.code).toBe('artifact.object_missing');
  });
});
