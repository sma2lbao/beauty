import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  isAuthenticated: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => 'logto-access-token'),
  getIdTokenClaims: vi.fn(async () => ({ sub: 'u1', email: 'a@b.c' })),
  signIn: vi.fn(async () => {}),
  signOut: vi.fn(async () => {}),
  handleSignInCallback: vi.fn(async () => {}),
}));

vi.mock('@logto/browser', () => ({
  default: class {
    constructor(public config: unknown) {}
    isAuthenticated = mocks.isAuthenticated;
    getAccessToken = mocks.getAccessToken;
    getIdTokenClaims = mocks.getIdTokenClaims;
    signIn = mocks.signIn;
    signOut = mocks.signOut;
    handleSignInCallback = mocks.handleSignInCallback;
  },
}));

/** 按指定模式重新加载鉴权模块(模式是模块加载期读取的)。 */
async function loadAuth(
  mode: 'dev' | 'token' | 'logto',
  extra: Record<string, string> = {},
) {
  vi.resetModules();
  vi.stubEnv('VITE_AUTH_MODE', mode);
  for (const [key, value] of Object.entries(extra)) {
    vi.stubEnv(key, value);
  }
  return await import('@/lib/auth');
}

const LOGTO_ENV = {
  VITE_LOGTO_ENDPOINT: 'https://idp.example',
  VITE_LOGTO_APP_ID: 'app-1',
};

describe('authHeaders', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    mocks.isAuthenticated.mockResolvedValue(true);
    window.localStorage.clear();
  });

  it('dev 模式:退回临时租户头', async () => {
    const auth = await loadAuth('dev', { VITE_TENANT_ID: 't-demo' });
    expect(await auth.authHeaders()).toEqual({ 'X-Tenant-Id': 't-demo' });
  });

  it('token 模式:有令牌走 Bearer,无令牌不带鉴权头', async () => {
    const auth = await loadAuth('token');
    expect(await auth.authHeaders()).toEqual({});

    auth.setStoredToken('manual-jwt');
    expect(await auth.authHeaders()).toEqual({
      Authorization: 'Bearer manual-jwt',
    });
    expect(await auth.accountLabel()).toBe('手动令牌');

    auth.setStoredToken(null);
    expect(await auth.authHeaders()).toEqual({});
  });

  it('logto 模式:已登录走 Bearer,未登录不带头且不抛错', async () => {
    const auth = await loadAuth('logto', LOGTO_ENV);
    expect(await auth.authHeaders()).toEqual({
      Authorization: 'Bearer logto-access-token',
    });
    expect(await auth.accountLabel()).toBe('a@b.c');

    mocks.isAuthenticated.mockResolvedValue(false);
    expect(await auth.authHeaders()).toEqual({});
    expect(await auth.accountLabel()).toBeNull();
  });

  it('logto:signIn 使用 /callback 回调地址,handleSignInCallback 透传 href', async () => {
    const auth = await loadAuth('logto', LOGTO_ENV);
    await auth.signIn();
    expect(mocks.signIn).toHaveBeenCalledWith({
      redirectUri: expect.stringContaining('/callback'),
    });

    await auth.handleSignInCallback('http://localhost/callback?code=abc');
    expect(mocks.handleSignInCallback).toHaveBeenCalledWith(
      'http://localhost/callback?code=abc',
    );
  });
});
