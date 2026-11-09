import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  init: vi.fn(),
  getTraceData: vi.fn(),
  browserTracingIntegration: vi.fn(() => ({ name: 'BrowserTracing' })),
}));

vi.mock('@sentry/react', () => ({
  init: mocks.init,
  getTraceData: mocks.getTraceData,
  browserTracingIntegration: mocks.browserTracingIntegration,
}));

type Telemetry = typeof import('@/lib/telemetry');

async function loadTelemetry(): Promise<Telemetry> {
  vi.resetModules();
  return import('@/lib/telemetry');
}

beforeEach(() => {
  mocks.init.mockClear();
  mocks.getTraceData.mockReset();
  mocks.browserTracingIntegration.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('initTelemetry', () => {
  it('无 DSN 时完全不初始化', async () => {
    const telemetry = await loadTelemetry();
    expect(telemetry.initTelemetry()).toBe(false);
    expect(mocks.init).not.toHaveBeenCalled();
  });

  it('有 DSN 时初始化并启用脱敏钩子,重复调用只生效一次', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://public@example.com/1');
    const telemetry = await loadTelemetry();

    expect(telemetry.initTelemetry()).toBe(true);
    expect(telemetry.initTelemetry()).toBe(false);
    expect(mocks.init).toHaveBeenCalledTimes(1);
    const options = mocks.init.mock.calls[0]?.[0] as {
      dsn: string;
      dataCollection: {
        userInfo: boolean;
        cookies: boolean;
        httpHeaders: { request: boolean; response: boolean };
        httpBodies: string[];
        urlQueryParams: boolean;
      };
      tracesSampleRate: number;
      integrations: unknown[];
      beforeSend: (event: unknown) => unknown;
      beforeBreadcrumb: (breadcrumb: unknown) => unknown;
    };
    expect(options.dsn).toBe('https://public@example.com/1');
    // v11 用 dataCollection 关闭默认 PII/body/header/query 采集
    expect(options.dataCollection).toEqual({
      userInfo: false,
      cookies: false,
      httpHeaders: { request: false, response: false },
      httpBodies: [],
      urlQueryParams: false,
    });
    // 测试环境是 dev,默认采样率 1.0
    expect(options.tracesSampleRate).toBe(1);
    expect(options.integrations).toHaveLength(1);
    expect(typeof options.beforeSend).toBe('function');
    expect(typeof options.beforeBreadcrumb).toBe('function');
  });
});

describe('事件脱敏', () => {
  it('删除请求体/cookie/query,敏感头与键抹值,URL 去 query,超长内容截断', async () => {
    const telemetry = await loadTelemetry();
    const longText = 'x'.repeat(2001);
    const event = {
      request: {
        url: 'https://app.example.com/runs?token=secret#frag',
        headers: {
          Authorization: 'Bearer secret',
          Accept: 'application/json',
        },
        cookies: { session: 'secret' },
        query_string: 'token=secret',
        data: { prompt: '用户输入' },
      },
      extra: {
        nested: { api_key: 'sk-secret', count: 3 },
        longText,
      },
      contexts: {
        app: { access_token: 'secret', route: '/runs' },
      },
      breadcrumbs: [
        {
          category: 'console',
          message: '用户输入的内容',
          data: { level: 'log' },
        },
        {
          category: 'fetch',
          message: 'GET /api/v1/runs',
          data: { status_code: 500, password: 'secret' },
        },
      ],
    };

    const redacted = telemetry.redactSentryEvent(
      event as unknown as Parameters<Telemetry['redactSentryEvent']>[0],
    );

    expect(redacted.request?.data).toBeUndefined();
    expect(redacted.request?.cookies).toBeUndefined();
    expect(redacted.request?.query_string).toBeUndefined();
    expect(redacted.request?.headers?.Authorization).toBe('[redacted]');
    expect(redacted.request?.headers?.Accept).toBe('application/json');
    expect(redacted.request?.url).toBe('https://app.example.com/runs');

    const extra = redacted.extra as {
      nested: { api_key: string; count: number };
      longText: string;
    };
    expect(extra.nested.api_key).toBe('[redacted]');
    expect(extra.nested.count).toBe(3);
    expect(extra.longText).toHaveLength(2000 + '…[truncated]'.length);

    const contexts = redacted.contexts as {
      app: { access_token: string; route: string };
    };
    expect(contexts.app.access_token).toBe('[redacted]');
    expect(contexts.app.route).toBe('/runs');

    const [consoleCrumb, fetchCrumb] = redacted.breadcrumbs ?? [];
    expect(consoleCrumb?.message).toBe('[console]');
    expect(consoleCrumb?.data).toBeUndefined();
    expect(
      (fetchCrumb?.data as { password: string; status_code: number }).password,
    ).toBe('[redacted]');
    expect(
      (fetchCrumb?.data as { password: string; status_code: number })
        .status_code,
    ).toBe(500);
  });
});

describe('traceparentHeader', () => {
  it('采样命中(01)时返回 W3C traceparent', async () => {
    mocks.getTraceData.mockReturnValue({
      traceparent:
        '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',
    });
    const telemetry = await loadTelemetry();
    expect(telemetry.traceparentHeader()).toEqual({
      traceparent:
        '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',
    });
    expect(mocks.getTraceData).toHaveBeenCalledWith({
      propagateTraceparent: true,
    });
  });

  it('未采样(00)/无数据/抛错时不带 traceparent', async () => {
    const telemetry = await loadTelemetry();
    mocks.getTraceData.mockReturnValueOnce({
      traceparent:
        '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00',
    });
    expect(telemetry.traceparentHeader()).toEqual({});
    mocks.getTraceData.mockReturnValueOnce({});
    expect(telemetry.traceparentHeader()).toEqual({});
    mocks.getTraceData.mockImplementationOnce(() => {
      throw new Error('sdk not ready');
    });
    expect(telemetry.traceparentHeader()).toEqual({});
  });
});
