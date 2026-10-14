import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { handleSignInCallback, takeLoginRedirect } from '@/lib/auth';

import { CallbackPage } from './callback';

vi.mock('@/lib/auth', () => ({
  handleSignInCallback: vi.fn(),
  takeLoginRedirect: vi.fn(() => '/'),
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

  it('回调成功后回到登录来路', async () => {
    vi.mocked(handleSignInCallback).mockResolvedValue();
    vi.mocked(takeLoginRedirect).mockReturnValue('/knowledge');
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<CallbackPage />} />
          <Route path="/knowledge" element={<div>知识库占位</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('知识库占位')).toBeInTheDocument();
    expect(takeLoginRedirect).toHaveBeenCalled();
  });
});
