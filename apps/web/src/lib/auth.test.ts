import { afterEach, describe, expect, it, vi } from 'vitest';

/** 按指定模式重新加载鉴权模块(模式是模块加载期读取的)。 */
async function loadAuth(mode: 'token' | 'session') {
  vi.resetModules();
  vi.stubEnv('VITE_AUTH_MODE', mode);
  return await import('@/lib/auth');
}

describe('authHeaders', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    window.localStorage.clear();
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
    const fetchSpy = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: 'new-access',
            refresh_token: 'new-refresh',
          }),
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
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline'))),
    );

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
        JSON.stringify({
          access_token: 'new-access',
          refresh_token: 'new-refresh',
        }),
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

describe('session 模式(API 网关 Cookie)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    document.cookie = 'bm_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  it('accessToken 走 session 探针,请求头带 CSRF', async () => {
    document.cookie = 'bm_csrf=csrf-1; path=/';
    const auth = await loadAuth('session');
    const fetchSpy = vi.fn(
      async () =>
        new Response(JSON.stringify({ authenticated: true }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    expect(await auth.accessToken()).toBe('session');
    expect(await auth.authHeaders()).toEqual({ 'X-CSRF-Token': 'csrf-1' });
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/auth/session',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('sessionLogin 提交到 API 网关且不保存本地令牌', async () => {
    const auth = await loadAuth('session');
    const fetchSpy = vi.fn(
      async () =>
        new Response(JSON.stringify({ authenticated: true }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    await auth.sessionLogin('a@b.c', 'password123');

    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    );
    expect(auth.getStoredToken()).toBeNull();
  });

  it('refresh 走 /api/auth/refresh,失败返回 false', async () => {
    const auth = await loadAuth('session');
    const fetchSpy = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchSpy);
    expect(await auth.refreshAccessToken()).toBe(true);

    fetchSpy.mockResolvedValue(new Response(null, { status: 401 }));
    expect(await auth.refreshAccessToken()).toBe(false);
  });
});
