/**
 * 前端观测入口(仅 apps/web):Sentry 错误/性能事件 + 与后端 OTLP trace 的关联。
 *
 * - `VITE_SENTRY_DSN` 为空时完全不初始化,不发任何网络请求(内网形态零副作用);
 * - 事件发送前统一脱敏:删除 cookie/请求体/query,敏感头与键抹值,URL 去 query/hash,
 *   超长字符串与超长数组截断——与后端 `common/redaction.py` 同一原则;
 * - `traceparentHeader()` 把 Sentry 当前 trace(Sentry 自己的 32 位 trace_id)转成
 *   W3C traceparent 带给后端;后端 FastAPI + OTel 会把它当父上下文,于是前端 Sentry
 *   trace 与服务端 Langfuse trace 共享同一个 trace_id,可按 id 互相跳转。
 *   只有该 trace 被采样(sample flag=01)时才带,避免前端未采样把后端也拖成丢弃。
 */

import * as Sentry from '@sentry/react';

import {
  sentryDsn,
  sentryEnvironment,
  sentryRelease,
  sentryTracesSampleRate,
} from '@/config/env';

/** 只匹配凭证类键;不匹配复数 `*_tokens`(用量计数)以免误伤。 */
const SENSITIVE_KEY =
  /authorization|cookie|password|passwd|secret|api[-_]?key|credential|access[-_]?token|refresh[-_]?token|id[-_]?token|bearer/i;

const MAX_STRING_CHARS = 2000;
const MAX_ARRAY_ITEMS = 64;
const MAX_DEPTH = 6;
const TRACEPARENT_PATTERN = /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/;

let initialized = false;

/** URL 去掉 query 与 hash(query 里可能带 token/code)。 */
export function stripUrl(url: string): string {
  try {
    const parsed = new URL(url, 'http://localhost');
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return url.split('?')[0]?.split('#')[0] ?? url;
  }
}

/** 递归脱敏:敏感键抹值、字符串/数组截断、限制嵌套深度。 */
export function redactValue(value: unknown, depth = 0): unknown {
  if (depth > MAX_DEPTH) {
    return '[truncated]';
  }
  if (typeof value === 'string') {
    return value.length > MAX_STRING_CHARS
      ? `${value.slice(0, MAX_STRING_CHARS)}…[truncated]`
      : value;
  }
  if (Array.isArray(value)) {
    return value
      .slice(0, MAX_ARRAY_ITEMS)
      .map((item) => redactValue(item, depth + 1));
  }
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = SENSITIVE_KEY.test(key)
        ? '[redacted]'
        : redactValue(item, depth + 1);
    }
    return result;
  }
  return value;
}

/** 发送前的错误/事务事件脱敏(导出给单测直接调用)。 */
export function redactSentryEvent(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  const request = event.request;
  if (request) {
    // 请求体可能是对话 prompt/文件内容;cookie 与 query 一律不带走。
    delete request.data;
    delete request.cookies;
    delete request.query_string;
    if (request.headers) {
      request.headers = Object.fromEntries(
        Object.entries(request.headers).map(([key, value]) => [
          key,
          SENSITIVE_KEY.test(key) ? '[redacted]' : value,
        ]),
      );
    }
    if (request.url) {
      request.url = stripUrl(request.url);
    }
  }
  if (event.extra) {
    event.extra = redactValue(event.extra) as typeof event.extra;
  }
  if (event.contexts) {
    event.contexts = redactValue(event.contexts) as typeof event.contexts;
  }
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs.map(redactBreadcrumb);
  }
  return event;
}

/**
 * 面包屑脱敏:console 面包屑默认丢弃正文(可能带用户输入),其余只清 URL 与敏感键。
 */
export function redactBreadcrumb(breadcrumb: Sentry.Breadcrumb): Sentry.Breadcrumb {
  const next: Sentry.Breadcrumb = { ...breadcrumb };
  if (next.data) {
    next.data = redactValue(next.data) as typeof next.data;
  }
  if (next.category === 'console') {
    next.message = '[console]';
    next.data = undefined;
  }
  return next;
}

function clampSampleRate(rate: number): number {
  if (!Number.isFinite(rate)) {
    return 0;
  }
  return Math.min(Math.max(rate, 0), 1);
}

/**
 * 初始化 Sentry;无 DSN 直接 no-op。重复调用只生效一次(React 严格模式/热更新安全)。
 *
 * @returns 是否真正初始化。
 */
export function initTelemetry(): boolean {
  if (initialized || !sentryDsn) {
    return false;
  }
  initialized = true;
  Sentry.init({
    dsn: sentryDsn,
    environment: sentryEnvironment,
    release: sentryRelease,
    // v11 用 dataCollection 取代 sendDefaultPii:关闭自动用户信息、cookie、请求/响应头与
    // 请求体、URL query 采集;页面不主动 setUser,错误事件里只剩匿名 id。
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: { request: false, response: false },
      httpBodies: [],
      urlQueryParams: false,
    },
    tracesSampleRate: clampSampleRate(sentryTracesSampleRate),
    integrations: [Sentry.browserTracingIntegration()],
    beforeSend: (event) => redactSentryEvent(event),
    beforeBreadcrumb: (breadcrumb) => redactBreadcrumb(breadcrumb),
  });
  return true;
}

/**
 * 当前页面的 W3C traceparent(采样命中时才有值),供 HTTP 层附带。
 *
 * Sentry 的 browserTracingIntegration 已经自动写 `sentry-trace`/`baggage`;
 * 这里补 `traceparent` 后,后端 OTel 才能把服务端 span 挂到同一个 trace_id 下。
 */
export function traceparentHeader(): Record<string, string> {
  try {
    const { traceparent } = Sentry.getTraceData({ propagateTraceparent: true });
    if (!traceparent || !TRACEPARENT_PATTERN.test(traceparent)) {
      return {};
    }
    return { traceparent };
  } catch {
    return {};
  }
}
