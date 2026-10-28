/**
 * C1 模型路由:可选模型目录的数据层契约(docs/design/model-routing.md §6.3)。
 * 断言端点、鉴权头与响应形状;空目录是合法结果(网关不可达时服务端回退配置)。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setStoredToken } from '@/lib/auth';
import { modelsApi } from '@/lib/models-api';

describe('modelsApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('走 /api/v1/models 并返回 default_model 与 models[].owned_by', async () => {
    setStoredToken('test-jwt');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        default_model: 'deepseek-chat',
        models: [
          { id: 'deepseek-chat', owned_by: 'deepseek' },
          { id: 'gpt-4o-mini', owned_by: 'openai' },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const catalog = await modelsApi.listModels();

    expect(catalog.default_model).toBe('deepseek-chat');
    expect(catalog.models.map((model) => model.id)).toEqual([
      'deepseek-chat',
      'gpt-4o-mini',
    ]);
    expect(catalog.models[1].owned_by).toBe('openai');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/models');
    expect((init.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer test-jwt',
    );
  });

  it('空目录(网关不可达/未配置)是合法结果,不是 404', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ default_model: null, models: [] }),
      }),
    );

    await expect(modelsApi.listModels()).resolves.toEqual({
      default_model: null,
      models: [],
    });
  });

  it('后端错误契约映射为 ApiError(code/message)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          code: 'llm.unavailable',
          message: '模型网关不可用',
        }),
      }),
    );

    await expect(modelsApi.listModels()).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      code: 'llm.unavailable',
      message: '模型网关不可用',
    });
  });
});
