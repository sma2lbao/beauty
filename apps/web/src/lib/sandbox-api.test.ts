import { afterEach, describe, expect, it, vi } from 'vitest';

import { setStoredToken } from './auth';
import {
  conversationSandbox,
  execSandbox,
  listSandboxes,
  readSandboxFile,
  sandboxTree,
} from './sandbox-api';

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

describe('sandboxApi', () => {
  it('沙箱列表走 /workspace-api 前缀并归一成 camelCase', async () => {
    setStoredToken('test-jwt');
    const fetchMock = mockFetch(200, [
      {
        id: 'sb-1',
        name: 'conv-1',
        status: 'running',
        scope_key: 'conv:conv-1',
        workspace_id: 'ws-1',
        run_id: null,
        created_at: '2026-10-01T00:00:00Z',
        last_error: null,
      },
    ]);

    const [sandbox] = await listSandboxes();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/workspace-api/v1/sandboxes');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-jwt',
    });
    expect(sandbox).toMatchObject({
      id: 'sb-1',
      scopeKey: 'conv:conv-1',
      workspaceId: 'ws-1',
      runId: null,
      status: 'running',
    });
  });

  it('按 scope_key 精确过滤', async () => {
    const fetchMock = mockFetch(200, []);

    await listSandboxes('conv:conv-1');

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/sandboxes?scope_key=conv%3Aconv-1',
    );
  });

  it('会话沙箱只认归属正确且 running 的那个', async () => {
    mockFetch(200, [
      { id: 'sb-old', status: 'expired', scope_key: 'conv:conv-1' },
      { id: 'sb-other', status: 'running', scope_key: 'conv:other' },
      { id: 'sb-1', status: 'running', scope_key: 'conv:conv-1' },
    ]);

    const sandbox = await conversationSandbox('conv-1');

    expect(sandbox?.id).toBe('sb-1');
  });

  it('没有运行中的沙箱时返回 null,不猜', async () => {
    mockFetch(200, [{ id: 'sb-old', status: 'expired', scope_key: 'conv:conv-1' }]);

    expect(await conversationSandbox('conv-1')).toBeNull();
  });

  it('tree 端点带 path 与 depth,并归一字段', async () => {
    const fetchMock = mockFetch(200, {
      path: '/workspace/src',
      entries: [
        {
          path: '/workspace/src/a.ts',
          name: 'a.ts',
          kind: 'file',
          size_bytes: 12,
          modified: 1_700_000_000,
        },
        { path: '/workspace/src/b.ts' },
      ],
      truncated: true,
    });

    const tree = await sandboxTree('sb-1', { path: '/workspace/src', depth: 2 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/sandboxes/sb-1/tree?path=%2Fworkspace%2Fsrc&depth=2',
    );
    expect(tree.truncated).toBe(true);
    expect(tree.entries[0]).toMatchObject({
      path: '/workspace/src/a.ts',
      kind: 'file',
      sizeBytes: 12,
      modified: 1_700_000_000,
    });
    // 缺 kind/name 时降级成 other + 从 path 推名字
    expect(tree.entries[1]).toMatchObject({ name: 'b.ts', kind: 'other' });
  });

  it('读文件把 base64 解码成文本', async () => {
    const fetchMock = mockFetch(200, {
      path: '/workspace/README.md',
      content_base64: btoa('hello'),
      size_bytes: 5,
    });

    const file = await readSandboxFile('sb-1', '/workspace/README.md');

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/workspace-api/v1/sandboxes/sb-1/files?path=%2Fworkspace%2FREADME.md',
    );
    expect(file).toEqual({
      path: '/workspace/README.md',
      sizeBytes: 5,
      binary: false,
      text: 'hello',
    });
  });

  it('含 NUL 的文件按二进制处理,不给正文', async () => {
    mockFetch(200, {
      path: '/workspace/logo.png',
      content_base64: btoa('a\u0000b'),
      size_bytes: 3,
    });

    const file = await readSandboxFile('sb-1', '/workspace/logo.png');

    expect(file.binary).toBe(true);
    expect(file.text).toBe('');
  });

  it('exec 提交 argv/cwd 并归一结果', async () => {
    const fetchMock = mockFetch(200, {
      exit_code: 2,
      stdout: 'out',
      stderr: 'err',
      duration_ms: 34,
      truncated: true,
    });

    const result = await execSandbox('sb-1', ['sh', '-lc', 'ls']);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/workspace-api/v1/sandboxes/sb-1/exec');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      argv: ['sh', '-lc', 'ls'],
      cwd: '/workspace',
    });
    expect(result).toEqual({
      exitCode: 2,
      stdout: 'out',
      stderr: 'err',
      durationMs: 34,
      truncated: true,
    });
  });
});
