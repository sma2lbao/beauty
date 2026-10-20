import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  isHttpUrl,
  isStdioCommand,
  mcpApi,
  parseArgsText,
  parseEnvText,
  type MCPServerCreateInput,
} from './mcp-api';

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function bodyOf(fetchMock: ReturnType<typeof mockFetch>, index = 0): unknown {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('mcpApi', () => {
  it('列表/测试/删除', async () => {
    const listMock = mockFetch(200, []);
    await mcpApi.list();
    expect(listMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers');

    const testMock = mockFetch(200, { ok: true, detail: '', tools: [] });
    await mcpApi.test('m1');
    expect(testMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers/m1/test');

    const deleteMock = mockFetch(204);
    await mcpApi.remove('m1');
    expect(deleteMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers/m1');
  });

  it('注册 HTTP server 只发送 http 字段', async () => {
    const createMock = mockFetch(201, { id: 'm1' });
    await mcpApi.create({
      transport: 'http',
      name: 'demo',
      url: 'http://127.0.0.1:8010/mcp',
      token: 'secret',
    });

    const [createUrl, createInit] = createMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(createUrl).toBe('/api/v1/mcp/servers');
    expect(createInit.method).toBe('POST');
    expect(bodyOf(createMock)).toEqual({
      transport: 'http',
      name: 'demo',
      url: 'http://127.0.0.1:8010/mcp',
      token: 'secret',
    });
  });

  it('注册 stdio server 只发送进程字段(不传 url/token)', async () => {
    const input: MCPServerCreateInput = {
      transport: 'stdio',
      name: 'filesystem',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
      env: { GITHUB_TOKEN: 'ghp_x' },
      runtime: 'node',
      image: 'node:22-slim',
    };
    const createMock = mockFetch(201, { id: 'm2' });
    await mcpApi.create(input);

    const body = bodyOf(createMock) as Record<string, unknown>;
    expect(body).toEqual(input);
    expect(body).not.toHaveProperty('url');
    expect(body).not.toHaveProperty('token');
  });

  it('更新仍只支持 enabled/url 补丁', async () => {
    const updateMock = mockFetch(200, { id: 'm1', enabled: false });
    await mcpApi.update('m1', { enabled: false, url: null });
    expect(updateMock.mock.calls[0]?.[0]).toBe('/api/v1/mcp/servers/m1');
    expect((updateMock.mock.calls[0]?.[1] as RequestInit).method).toBe('PATCH');
    expect(bodyOf(updateMock)).toEqual({ enabled: false, url: null });
  });
});

describe('stdio 表单解析', () => {
  it('参数一行一个,忽略空行并去掉首尾空白', () => {
    expect(
      parseArgsText(' -y \n\n@modelcontextprotocol/server-filesystem\n/tmp '),
    ).toEqual(['-y', '@modelcontextprotocol/server-filesystem', '/tmp']);
    expect(parseArgsText('')).toEqual([]);
  });

  it('环境变量按 KEY=VALUE 解析,忽略注释与非法行', () => {
    expect(
      parseEnvText(
        [
          '# 注释行',
          'GITHUB_TOKEN=ghp_x',
          'EMPTY=',
          'SPACED = value with spaces ',
          'NO_SEPARATOR',
          '=missing-key',
          '',
        ].join('\n'),
      ),
    ).toEqual({
      GITHUB_TOKEN: 'ghp_x',
      EMPTY: '',
      SPACED: 'value with spaces',
    });
  });

  it('stdio 命令只允许可执行名与相对路径', () => {
    expect(isStdioCommand('npx')).toBe(true);
    expect(isStdioCommand(' uvx ')).toBe(true);
    expect(isStdioCommand('/usr/local/bin/uvx')).toBe(true);
    expect(isStdioCommand('npx -y')).toBe(false);
    expect(isStdioCommand('bash -c "rm -rf /"')).toBe(false);
    expect(isStdioCommand('')).toBe(false);
  });

  it('HTTP URL 校验只接受 http(s)', () => {
    expect(isHttpUrl('http://127.0.0.1:8010/mcp')).toBe(true);
    expect(isHttpUrl(' https://example.com/mcp ')).toBe(true);
    expect(isHttpUrl('ftp://example.com/mcp')).toBe(false);
    expect(isHttpUrl('example.com/mcp')).toBe(false);
    expect(isHttpUrl('http://')).toBe(false);
  });
});
