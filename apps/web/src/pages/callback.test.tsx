import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { handleSignInCallback } from '@/lib/auth';

import { CallbackPage } from './callback';

vi.mock('@/lib/auth', () => ({
  handleSignInCallback: vi.fn(),
}));

describe('CallbackPage', () => {
  it('处理授权回调并提示进度', async () => {
    vi.mocked(handleSignInCallback).mockResolvedValue();
    render(
      <MemoryRouter>
        <CallbackPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole('heading', { name: '正在完成登录…' }),
    ).toBeInTheDocument();
    expect(handleSignInCallback).toHaveBeenCalledWith(window.location.href);
  });

  it('回调失败时展示错误', async () => {
    vi.mocked(handleSignInCallback).mockRejectedValue(new Error('回调无效'));
    render(
      <MemoryRouter>
        <CallbackPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('回调无效')).toBeInTheDocument();
  });
});
