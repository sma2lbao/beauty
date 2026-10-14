/**
 * 前端统一 HTTP 层:各控制台共用错误契约与请求封装(架构 §11)。
 *
 * - 后端错误体统一是 `{"code","message","details"}`,这里映射成 `ApiError`;
 * - 鉴权头由 `@/lib/auth` 决定(有 token 走 Bearer;未登录请求由守卫拦下);
 * - access token 过期(401)且存有 refresh token 时自动续期并重试一次
 *   (单飞:`@/lib/auth` 保证并发 401 只发一次刷新请求);
 * - 各服务只提供 base 与路径(dev 由 vite.config.ts 代理到对应端口)。
 */

import { authHeaders, refreshAccessToken } from '@/lib/auth';

/** 后端统一错误契约(§11)。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

/** 401 判定:控制台据此提示"未登录/令牌失效"。 */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** JSON + 鉴权头(SSE 等自定义请求也复用它)。 */
export async function jsonHeaders(): Promise<Record<string, string>> {
  return { 'Content-Type': 'application/json', ...(await authHeaders()) };
}

/**
 * 发一个 JSON 请求:非 2xx 抛 `ApiError`;204 返回 undefined。
 * `path` 是服务内路径(如 `/v1/knowledge/documents`),`base` 由调用方决定。
 */
export async function requestJson<T>(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const send = async () =>
    fetch(`${base}${path}`, {
      ...init,
      headers: { ...(await jsonHeaders()), ...init?.headers },
    });
  let response = await send();
  if (response.status === 401 && (await refreshAccessToken())) {
    // 续期成功:换新 access token 重试一次;仍 401 则按错误契约抛出
    response = await send();
  }
  if (!response.ok) {
    let code = 'request_failed';
    let message = `请求失败(${response.status})`;
    try {
      const body = (await response.json()) as { code?: string; message?: string };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      // 非 JSON 错误体:保留兜底文案
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
