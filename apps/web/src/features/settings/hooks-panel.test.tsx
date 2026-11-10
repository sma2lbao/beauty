import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { hooksApi, type Hook, type HookSecret } from '@/lib/hooks-api';
import { ApiError } from '@/lib/http';

import { HooksPanel } from './hooks-panel';

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

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <HooksPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(hooksApi.list).mockResolvedValue([hook]);
  vi.mocked(hooksApi.create).mockResolvedValue(withSecret);
  vi.mocked(hooksApi.update).mockResolvedValue(hook);
  vi.mocked(hooksApi.remove).mockResolvedValue(undefined);
  vi.mocked(hooksApi.rotate).mockResolvedValue({
    ...withSecret,
    secret: 'rotated',
  });
});

describe('HooksPanel', () => {
  it('表格展示事件中文名、target 摘要、超时与启用开关', async () => {
    renderPanel();

    expect(await screen.findByText('工具调用前')).toBeTruthy();
    expect(screen.getByText('example.com/dsh/hook')).toBeTruthy();
    expect(screen.getByText('3s')).toBeTruthy();
    expect(
      screen.getByRole('switch', { name: '启用 工具调用前 hook' }),
    ).toHaveAttribute('aria-checked', 'true');
    expect(
      screen.getByLabelText('轮换 工具调用前 hook 的签名密钥'),
    ).toBeTruthy();
    expect(screen.getByLabelText('删除 工具调用前 hook')).toBeTruthy();
  });

  it('没有 hook 时显示空态说明', async () => {
    vi.mocked(hooksApi.list).mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByText(/还没有 hook/)).toBeTruthy();
  });

  it('部署未启用时整卡显示说明而不是静默失败', async () => {
    vi.mocked(hooksApi.list).mockRejectedValue(
      new ApiError(409, 'hooks.disabled', 'hooks disabled'),
    );

    renderPanel();

    expect(
      await screen.findByText(/当前部署未启用 hooks/),
    ).toBeTruthy();
  });

  it('创建成功弹一次性 secret,复制并关闭后不再显示', async () => {
    vi.mocked(hooksApi.list).mockResolvedValue([]);

    renderPanel();
    await screen.findByText(/还没有 hook/);

    fireEvent.change(screen.getByLabelText('目标地址(HTTPS)'), {
      target: { value: 'https://example.com/dash' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建 hook' }));

    expect(await screen.findByText(/hook 签名密钥/)).toBeTruthy();
    expect(screen.getByText('s3cret')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '复制密钥' }));
    expect(await screen.findByText('已复制到剪贴板')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '我已保存,关闭' }));
    await waitFor(() =>
      expect(screen.queryByText(/hook 签名密钥/)).toBeNull(),
    );
  });

  it('后端拒绝目标地址时就地显示错误', async () => {
    vi.mocked(hooksApi.list).mockResolvedValue([]);
    vi.mocked(hooksApi.create).mockRejectedValue(
      new ApiError(422, 'hooks.invalid_target', 'invalid target'),
    );

    renderPanel();
    await screen.findByText(/还没有 hook/);

    fireEvent.change(screen.getByLabelText('目标地址(HTTPS)'), {
      target: { value: 'https://example.com/dash' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建 hook' }));

    expect(await screen.findByText(/目标地址无效/)).toBeTruthy();
  });

  it('删除与轮换按钮调用对应接口', async () => {
    renderPanel();
    await screen.findByText('工具调用前');

    fireEvent.click(screen.getByLabelText('删除 工具调用前 hook'));
    await waitFor(() =>
      expect(hooksApi.remove).toHaveBeenCalledWith('hook-1'),
    );

    fireEvent.click(
      screen.getByLabelText('轮换 工具调用前 hook 的签名密钥'),
    );
    await waitFor(() =>
      expect(hooksApi.rotate).toHaveBeenCalledWith('hook-1'),
    );
    expect(await screen.findByText('rotated')).toBeTruthy();
  });
});
