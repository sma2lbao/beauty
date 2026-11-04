import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  conversationsApi,
  type ToolConfirmationResult,
} from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';

import { ToolConfirmationCard } from './tool-confirmation-card';

type CardProps = Parameters<typeof ToolConfirmationCard>[0];

function renderCard(overrides: Partial<CardProps> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const onSettled = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <ToolConfirmationCard
        conversationId="c1"
        confirmationId="cfm-1"
        argumentsText={'{"page_id":"p1","title":"周报"}'}
        onSettled={onSettled}
        {...overrides}
      />
    </QueryClientProvider>,
  );
  return { queryClient, invalidate, onSettled };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ToolConfirmationCard', () => {
  it('渲染写入提示、参数摘要、有效期与两个动作', () => {
    renderCard({ expiresAt: '2026-11-04T21:40:00+08:00' });

    expect(
      screen.getByText('该操作会写入外部服务,确认后才会执行'),
    ).toBeTruthy();
    expect(screen.getByText('page_id=p1 · title=周报')).toBeTruthy();
    expect(screen.getByText(/确认有效期至/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '确认执行' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy();
  });

  it('确认成功:展示结果、通知父卡片并刷新会话消息', async () => {
    const result: ToolConfirmationResult = {
      confirmation_id: 'cfm-1',
      status: 'confirmed',
      ok: true,
      result_text: '已创建页面《周报》',
      message_id: 'm2',
    };
    vi.spyOn(conversationsApi, 'confirmToolCall').mockResolvedValue(result);
    const { invalidate, onSettled } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));

    expect(await screen.findByText('已创建页面《周报》')).toBeTruthy();
    expect(conversationsApi.confirmToolCall).toHaveBeenCalledWith(
      'c1',
      'cfm-1',
    );
    expect(screen.getByText('执行完成')).toBeTruthy();
    expect(screen.getByText('已写入会话消息')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull();
    expect(onSettled).toHaveBeenCalledWith('confirmed');
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['conversation-messages', 'c1'],
    });
  });

  it('确认执行失败(ok=false):仍刷新并展示错误码', async () => {
    vi.spyOn(conversationsApi, 'confirmToolCall').mockResolvedValue({
      confirmation_id: 'cfm-1',
      status: 'confirmed',
      ok: false,
      error_code: 'connector.upstream_error',
      result_text: 'Notion 返回 500',
    });
    const { onSettled } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));

    expect(await screen.findByText('执行失败')).toBeTruthy();
    expect(screen.getByText('connector.upstream_error')).toBeTruthy();
    expect(screen.getByText('Notion 返回 500')).toBeTruthy();
    expect(onSettled).toHaveBeenCalledWith('failed');
  });

  it('确认中两个按钮都禁用,重复点击不会发第二次请求', async () => {
    let resolveConfirm!: (value: ToolConfirmationResult) => void;
    vi.spyOn(conversationsApi, 'confirmToolCall').mockReturnValue(
      new Promise<ToolConfirmationResult>((resolve) => {
        resolveConfirm = resolve;
      }),
    );
    renderCard();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));

    const confirming = screen.getByRole('button', {
      name: '确认中…',
    }) as HTMLButtonElement;
    expect(confirming.disabled).toBe(true);
    expect(
      (screen.getByRole('button', { name: '取消' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    fireEvent.click(confirming);
    expect(conversationsApi.confirmToolCall).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveConfirm({
        confirmation_id: 'cfm-1',
        status: 'confirmed',
        ok: true,
        result_text: 'ok',
      });
    });
    expect(screen.getByText('执行完成')).toBeTruthy();
  });

  it('取消成功:提示已取消,操作不会执行', async () => {
    vi.spyOn(conversationsApi, 'cancelToolCall').mockResolvedValue({
      confirmation_id: 'cfm-1',
      status: 'cancelled',
    });
    const { invalidate, onSettled } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(await screen.findByText('已取消,该操作未执行。')).toBeTruthy();
    expect(conversationsApi.cancelToolCall).toHaveBeenCalledWith('c1', 'cfm-1');
    expect(onSettled).toHaveBeenCalledWith('cancelled');
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['conversation-messages', 'c1'],
    });
  });

  it('409 已过期:提示过期并刷新会话,不再保留按钮', async () => {
    vi.spyOn(conversationsApi, 'confirmToolCall').mockRejectedValue(
      new ApiError(409, 'tool_confirmation.expired', '确认已过期'),
    );
    const { invalidate, onSettled } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));

    expect(
      await screen.findByText('确认已过期,请重新发起该操作。'),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull();
    expect(onSettled).toHaveBeenCalledWith('resolved');
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['conversation-messages', 'c1'],
    });
  });

  it('409 已处理:提示已处理并刷新会话', async () => {
    vi.spyOn(conversationsApi, 'cancelToolCall').mockRejectedValue(
      new ApiError(409, 'tool_confirmation.not_pending', '该确认已处理'),
    );
    const { onSettled } = renderCard();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(await screen.findByText('该操作已处理,会话已刷新。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '取消' })).toBeNull();
    expect(onSettled).toHaveBeenCalledWith('resolved');
  });
});
