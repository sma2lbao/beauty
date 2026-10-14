import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthGuard } from './auth-guard';
import { accessToken, authMode } from '@/lib/auth';

vi.mock('@/lib/auth', () => ({
  authMode: vi.fn(() => 'token'),
  accessToken: vi.fn(async () => null),
}));

/** 登录页探针:把守卫送来时的 state.from.pathname 显示出来。 */
function LoginProbe() {
  const location = useLocation();
  const from = (location.state as { from?: { pathname?: string } } | null)
    ?.from?.pathname;
  return <div data-testid="login-probe">{from ?? '(无来路)'}</div>;
}

function renderGuard(path = '/secret') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AuthGuard />}>
          <Route path="/secret" element={<div>受保护内容</div>} />
        </Route>
        <Route path="/login" element={<LoginProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(authMode).mockReturnValue('token');
  vi.mocked(accessToken).mockResolvedValue(null);
});

describe('AuthGuard', () => {
  it('dev 模式同步放行(不闪 loading)', () => {
    vi.mocked(authMode).mockReturnValue('dev');
    renderGuard();
    expect(screen.getByText('受保护内容')).toBeInTheDocument();
  });

  it('token 模式无令牌:送回 /login 并记住来路', async () => {
    renderGuard('/secret');
    expect(await screen.findByTestId('login-probe')).toHaveTextContent(
      '/secret',
    );
  });

  it('token 模式有令牌:放行受保护页', async () => {
    vi.mocked(accessToken).mockResolvedValue('access-jwt');
    renderGuard();
    expect(await screen.findByText('受保护内容')).toBeInTheDocument();
  });

  it('校验抛错按未登录处理', async () => {
    vi.mocked(accessToken).mockRejectedValue(new Error('IdP 不可达'));
    renderGuard();
    expect(await screen.findByTestId('login-probe')).toBeInTheDocument();
  });
});
