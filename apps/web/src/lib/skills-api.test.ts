import { afterEach, describe, expect, it, vi } from 'vitest';

import { skillsApi } from './skills-api';

function mockFetch(status = 200, body?: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status < 400,
    status,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function callOf(
  fetchMock: ReturnType<typeof mockFetch>,
  index = 0,
): [string, RequestInit] {
  return fetchMock.mock.calls[index] as unknown as [string, RequestInit];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('skillsApi', () => {
  it('列表/详情走 GET /api/v1/skills,按 JSON 发送', async () => {
    const listMock = mockFetch(200, []);
    await skillsApi.list();
    const [listUrl, listInit] = callOf(listMock);
    expect(listUrl).toBe('/api/v1/skills');
    expect((listInit.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );

    const getMock = mockFetch(200, { id: 'office.email-summary' });
    await skillsApi.get('office.email-summary');
    expect(callOf(getMock)[0]).toBe('/api/v1/skills/office.email-summary');
  });

  it('上传技能包:multipart 字段 file,不设置 JSON Content-Type', async () => {
    const fetchMock = mockFetch(201, { id: 'p1' });
    const file = new File(['# skill'], 'demo.zip', {
      type: 'application/zip',
    });

    await skillsApi.uploadPackage(file);

    const [url, init] = callOf(fetchMock);
    expect(url).toBe('/api/v1/skills/packages');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('file')).toBe(file);
    // Content-Type 必须留给浏览器带 boundary;请求头里不能出现 application/json
    const headerNames = Object.keys(init.headers as Record<string, string>).map(
      (name) => name.toLowerCase(),
    );
    expect(headerNames).not.toContain('content-type');
  });

  it('包列表/详情/删除', async () => {
    const listMock = mockFetch(200, []);
    await skillsApi.listPackages();
    expect(callOf(listMock)[0]).toBe('/api/v1/skills/packages');

    const getMock = mockFetch(200, { id: 'p1' });
    await skillsApi.getPackage('p1');
    expect(callOf(getMock)[0]).toBe('/api/v1/skills/packages/p1');

    const deleteMock = mockFetch(204);
    await skillsApi.deletePackage('p1');
    const [deleteUrl, deleteInit] = callOf(deleteMock);
    expect(deleteUrl).toBe('/api/v1/skills/packages/p1');
    expect(deleteInit.method).toBe('DELETE');
  });
});
