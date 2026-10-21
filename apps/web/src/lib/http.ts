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

/** 请求体是 FormData(文件上传):Content-Type 必须由浏览器带 boundary 生成。 */
function isFormDataBody(body: BodyInit | null | undefined): boolean {
  return typeof FormData !== 'undefined' && body instanceof FormData;
}

/** 带鉴权/CSRF 头发一次请求(不处理 401 续期)。 */
async function sendWithAuth(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const headers = isFormDataBody(init?.body)
    ? await authHeaders()
    : await jsonHeaders();
  return fetch(`${base}${path}`, {
    ...init,
    credentials: 'include',
    headers: { ...headers, ...init?.headers },
  });
}

/** 401 时用 refresh token 续期并重试一次;返回最终响应。 */
async function sendWithRetry(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<Response> {
  let response = await sendWithAuth(base, path, init);
  if (response.status === 401 && (await refreshAccessToken())) {
    // 续期成功:换新 access token 重试一次;仍 401 则按错误契约抛出
    response = await sendWithAuth(base, path, init);
  }
  return response;
}

/** 把非 2xx 响应映射成统一 `ApiError`(错误体可能是 JSON,也可能是空)。 */
async function throwResponseError(response: Response): Promise<never> {
  let code = 'request_failed';
  let message = `请求失败(${response.status})`;
  try {
    const body = (await response.json()) as {
      code?: string;
      message?: string;
    };
    code = body.code ?? code;
    message = body.message ?? message;
  } catch {
    // 非 JSON 错误体:保留兜底文案
  }
  throw new ApiError(response.status, code, message);
}

/**
 * 发一个 JSON 请求:非 2xx 抛 `ApiError`;204 返回 undefined。
 * `path` 是服务内路径(如 `/v1/knowledge/documents`),`base` 由调用方决定。
 *
 * 请求体是 `FormData` 时不写 Content-Type,交给浏览器生成
 * `multipart/form-data; boundary=…`;鉴权头/CSRF/401 续期逻辑完全一致。
 */
export async function requestJson<T>(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await sendWithRetry(base, path, init);
  if (!response.ok) {
    await throwResponseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/**
 * 发一个二进制下载请求:返回 `Blob`(响应体原样,不解析 JSON)。
 *
 * 下载端点可能是同源 BFF 代理或直连服务;鉴权头/CSRF/401 续期与
 * `requestJson` 完全一致,失败时同样映射成 `ApiError`。
 */
export async function requestBlob(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<Blob> {
  const response = await sendWithRetry(base, path, init);
  if (!response.ok) {
    await throwResponseError(response);
  }
  return response.blob();
}
