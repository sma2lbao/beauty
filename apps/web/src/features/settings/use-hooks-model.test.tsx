import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { hooksApi, type Hook, type HookSecret } from '@/lib/hooks-api';
import { ApiError } from '@/lib/http';

import {
  summarizeTarget,
  useHooksModel,
  validateHookForm,
} from './use-hooks-model';

vi.mock('@/lib/hooks-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/hooks-api')>(
      '@/lib/hooks-api',
    );
  return {
    ...actual,
    hooksApi: {
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      rotate: vi.fn(),
    },
  };
});

const hook: Hook = {
  id: 'hook-1',
  event: 'PreToolUse',
  matcher: '*',
  kind: 'webhook',
  target: 'https://example.com/dsh/hook',
  timeout_seconds: 3,
  enabled: true,
  created_at: '2026-11-10T00:00:00+00:00',
  updated_at: '2026-11-10T00:00:00+00:00',
};

const withSecret: HookSecret = { ...hook, secret: 's3cret' };

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useHooksModel(), { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(hooksApi.list).mockResolvedValue([hook]);
  vi.mocked(hooksApi.create).mockResolvedValue(withSecret);
  vi.mocked(hooksApi.update).mockResolvedValue(hook);
  vi.mocked(hooksApi.remove).mockResolvedValue(undefined);
  vi.mocked(hooksApi.rotate).mockResolvedValue({ ...withSecret, secret: 'rotated' });
});

describe('useHooksModel', () => {
  it('加载 hook 列表', async () => {
    const { result } = setup();

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hooks).toEqual([hook]);
  });

  it('创建成功后弹出一次性 secret 并重置表单', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.updateForm({ target: 'https://example.com/dash' });
    });
    act(() => {
      result.current.submit();
    });

    await waitFor(() =>
      expect(hooksApi.create).toHaveBeenCalledWith({
        event: 'PreToolUse',
        matcher: '*',
        target: 'https://example.com/dash',
        timeout_seconds: 3,
      }),
    );
    await waitFor(() => expect(result.current.secret?.secret).toBe('s3cret'));
    await waitFor(() => expect(result.current.form.target).toBe(''));
    // 创建成功后失效列表查询,触发重取
    await waitFor(() => expect(hooksApi.list).toHaveBeenCalledTimes(2));
  });

  it('非 HTTPS 目标在本地拦截,不发创建请求', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.updateForm({ target: 'http://example.com/dash' });
    });
    act(() => {
      result.current.submit();
    });

    expect(result.current.validationError).toMatch(/HTTPS/);
    expect(hooksApi.create).not.toHaveBeenCalled();
  });

  it('切换启用与删除调用 update/remove 并重取列表', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.toggleHook(hook, false);
    });
    await waitFor(() =>
      expect(hooksApi.update).toHaveBeenCalledWith('hook-1', {
        enabled: false,
      }),
    );

    act(() => {
      result.current.deleteHook('hook-1');
    });
    await waitFor(() => expect(hooksApi.remove).toHaveBeenCalledWith('hook-1'));
    await waitFor(() => expect(hooksApi.list).toHaveBeenCalledTimes(3));
  });

  it('轮换返回新 secret', async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.rotateHook('hook-1');
    });

    await waitFor(() =>
      expect(hooksApi.rotate).toHaveBeenCalledWith('hook-1'),
    );
    await waitFor(() => expect(result.current.secret?.secret).toBe('rotated'));
  });

  it('409 hooks.disabled 归为 disabled 态', async () => {
    vi.mocked(hooksApi.list).mockRejectedValue(
      new ApiError(409, 'hooks.disabled', 'hooks disabled'),
    );

    const { result } = setup();

    await waitFor(() => expect(result.current.disabled).toBe(true));
    expect(result.current.hooks).toEqual([]);
  });
});

describe('hooks 表单校验与目标摘要', () => {
  it('目标必须是 HTTPS 并限制超时范围', () => {
    expect(
      validateHookForm({
        event: 'PreToolUse',
        matcher: '*',
        target: 'http://example.com',
        timeout: '3',
      }),
    ).toMatch(/HTTPS/);
    expect(
      validateHookForm({
        event: 'PreToolUse',
        matcher: '*',
        target: 'https://example.com',
        timeout: '31',
      }),
    ).toMatch(/超时/);
    expect(
      validateHookForm({
        event: 'PreToolUse',
        matcher: '*',
        target: 'https://example.com',
        timeout: '3',
      }),
    ).toBeNull();
  });

  it('摘要只保留域名与路径', () => {
    expect(summarizeTarget('https://example.com/a/b')).toBe(
      'example.com/a/b',
    );
    expect(summarizeTarget('https://example.com')).toBe('example.com');
    expect(summarizeTarget('not-a-url')).toBe('not-a-url');
  });
});
