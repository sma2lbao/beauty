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

describe('双令牌存储(token 模式)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    window.localStorage.clear();
  });

  it('setStoredTokens 保存双令牌与账号标签', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('access-1', 'refresh-1', 'a@b.c');

    expect(auth.getStoredToken()).toBe('access-1');
    expect(auth.getStoredRefreshToken()).toBe('refresh-1');
    expect(await auth.accountLabel()).toBe('a@b.c');
  });

  it('setStoredToken 粘贴场景清掉旧 refresh 与标签(避免错配)', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('access-1', 'refresh-1', 'a@b.c');
    auth.setStoredToken('pasted-access');

    expect(auth.getStoredToken()).toBe('pasted-access');
    expect(auth.getStoredRefreshToken()).toBeNull();
    expect(await auth.accountLabel()).toBe('手动令牌');
  });
});

describe('refreshAccessToken(token 模式)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('没存 refresh token:不发请求,直接 false', async () => {
    const auth = await loadAuth('token');
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    expect(await auth.refreshAccessToken()).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('刷新成功:存新令牌对,返回 true', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('old-access', 'old-refresh', 'a@b.c');
    const fetchSpy = vi.fn(async () =>
      new Response(
        JSON.stringify({ access_token: 'new-access', refresh_token: 'new-refresh' }),
        { status: 200 },
      ),
    );
    vi.stubGlobal('fetch', fetchSpy);

    expect(await auth.refreshAccessToken()).toBe(true);
    expect(fetchSpy).toHaveBeenCalledWith(
      '/account-api/v1/auth/refresh',
      expect.objectContaining({ method: 'POST' }),
    );
    // 旋转:新旧 access/refresh 都更新,标签保留
    expect(auth.getStoredToken()).toBe('new-access');
    expect(auth.getStoredRefreshToken()).toBe('new-refresh');
    expect(await auth.accountLabel()).toBe('a@b.c');
  });

  it('刷新被拒(过期/吊销):清空本地令牌,返回 false', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('old-access', 'old-refresh', 'a@b.c');
    const fetchSpy = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ code: 'auth.refresh_reused', message: '失效' }),
          { status: 401 },
        ),
    );
    vi.stubGlobal('fetch', fetchSpy);

    expect(await auth.refreshAccessToken()).toBe(false);
    expect(auth.getStoredToken()).toBeNull();
    expect(auth.getStoredRefreshToken()).toBeNull();
  });

  it('网络错误:令牌保留,返回 false(下次再试)', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('old-access', 'old-refresh', 'a@b.c');
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))));

    expect(await auth.refreshAccessToken()).toBe(false);
    expect(auth.getStoredToken()).toBe('old-access');
    expect(auth.getStoredRefreshToken()).toBe('old-refresh');
  });

  it('并发刷新只发一次请求(单飞)', async () => {
    const auth = await loadAuth('token');
    auth.setStoredTokens('old-access', 'old-refresh', 'a@b.c');
    const fetchSpy = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return new Response(
        JSON.stringify({ access_token: 'new-access', refresh_token: 'new-refresh' }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchSpy);

    const [a, b, c] = await Promise.all([
      auth.refreshAccessToken(),
      auth.refreshAccessToken(),
      auth.refreshAccessToken(),
    ]);
    expect([a, b, c]).toEqual([true, true, true]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe('登录来路记忆', () => {
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it('记住并取出登录来路,取出即清掉', async () => {
    const auth = await loadAuth('dev');
    auth.rememberLoginRedirect('/runs');
    expect(auth.takeLoginRedirect()).toBe('/runs');
    // 已被消费,第二次取回默认值
    expect(auth.takeLoginRedirect()).toBe('/');
  });

  it('非站内路径一律回对话页(防开放重定向)', async () => {
    const auth = await loadAuth('dev');
    auth.rememberLoginRedirect('//evil.example');
    expect(auth.takeLoginRedirect()).toBe('/');
    expect(auth.takeLoginRedirect()).toBe('/');
  });
});
