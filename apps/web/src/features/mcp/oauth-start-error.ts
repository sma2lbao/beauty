/**
 * oauth/start 失败的分类映射(纯函数,便于单测)。
 *
 * 后端把「MCP 资源侧不可达 / 未公布 OAuth 元数据 / 元数据缺字段 /
 * 授权服务器不可达或不支持动态注册」拆成不同 502 错误码;前端按 code
 * 映射成「原因标题 + 具体下一步」,让用户一眼知道该改网络、改用静态令牌
 * 还是去找 server 维护者。details 里的 stage/reason 不参与映射(只认 code)。
 *
 * cta 语义:
 * - retry: 先重试授权(网络/注册临时故障);
 * - token: 浏览器授权走不通,改用静态令牌(「改用令牌」弹窗);
 * - none:  重试与令牌都无法解决(部署/配置问题),只给说明。
 */
import { ApiError } from '@/lib/http';

/** 失败原因分类,展示层据此决定提示色与动作。 */
export type OAuthStartErrorKind =
  | 'network'
  | 'not_oauth'
  | 'bad_metadata'
  | 'no_dcr'
  | 'registration_failed'
  | 'server_config'
  | 'unknown';

/** 推荐下一步动作。 */
export type OAuthStartCta = 'retry' | 'token' | 'none';

/** 分类结果:面板直接消费的完整文案(错误码始终保留,便于排查)。 */
export type OAuthStartErrorInfo = {
  kind: OAuthStartErrorKind;
  title: string;
  detail: string;
  cta: OAuthStartCta;
  code: string;
};

type Classification = Omit<OAuthStartErrorInfo, 'code'>;

/** 后端契约里「未知/兜底」错误码的占位值。 */
const UNKNOWN_CODE = 'unknown';

/**
 * 逐 code 映射(契约见任务提示;未知 code 落到 unknown,用后端 message 兜底)。
 * 同 kind 的两个授权服务器错误码共用一段文案;server_config 的两码语义不同,
 * 各自给说明(一个是部署开关,一个是 stdio 不支持 OAuth)。
 */
function classify(code: string): Classification {
  switch (code) {
    case 'mcp.oauth_discovery_unreachable':
    case 'mcp.oauth_authorization_server_unreachable':
      return {
        kind: 'network',
        title: '无法连接 MCP server',
        detail:
          '请求完全没有到达 server（网络不通、DNS 解析失败、连接超时或 TLS 证书问题）。请确认地址可从本机访问、必要时配置代理或信任证书，恢复后点「重试授权」；若该 server 只支持静态令牌，也可以改用令牌连接。',
        cta: 'retry',
      };
    case 'mcp.oauth_discovery_failed':
      return {
        kind: 'not_oauth',
        title: '该 server 未启用 OAuth 授权',
        detail:
          'server 可以访问，但没有公布 RFC 9728 OAuth 资源元数据，通常说明它只支持静态令牌。请点「改用令牌」粘贴访问令牌完成连接。',
        cta: 'token',
      };
    case 'mcp.oauth_discovery_invalid':
    case 'mcp.oauth_authorization_server_invalid':
      return {
        kind: 'bad_metadata',
        title: 'OAuth 元数据不合法',
        detail:
          'server 或它的授权服务器返回的 OAuth 元数据缺字段（如 authorization_endpoint、token_endpoint）。这通常是 server 侧配置问题：可改用静态令牌；若必须走 OAuth，请联系该 server 的维护者修正元数据。',
        cta: 'token',
      };
    case 'mcp.oauth_registration_unsupported':
      return {
        kind: 'no_dcr',
        title: '授权服务器不支持动态注册',
        detail:
          '授权服务器没有开放 RFC 7591 动态客户端注册，无法自动创建 OAuth 客户端。请改用静态令牌，或联系 server 维护者提供预注册的 client 后再授权。',
        cta: 'token',
      };
    case 'mcp.oauth_registration_failed':
      return {
        kind: 'registration_failed',
        title: 'OAuth 动态注册失败',
        detail:
          '向授权服务器注册 OAuth 客户端时失败（可能被拒绝或临时不可用）。稍后点「重试授权」；若持续失败，请改用静态令牌或联系 server 维护者。',
        cta: 'retry',
      };
    case 'mcp.oauth_disabled':
      return {
        kind: 'server_config',
        title: '该 server 的 OAuth 授权未启用',
        detail:
          '后端未开启 MCP OAuth 授权能力（部署配置问题），重试不会马上生效。请让管理员在服务端配置里启用 OAuth 后再试。',
        cta: 'none',
      };
    case 'mcp.oauth_stdio_unsupported':
      return {
        kind: 'server_config',
        title: '该传输方式不支持 OAuth',
        detail:
          'stdio（本地进程）server 不支持浏览器 OAuth 授权。请改在注册表单里用环境变量配置本地进程的凭证，或改用 HTTP server + 静态令牌。',
        cta: 'none',
      };
    default:
      return {
        kind: 'unknown',
        title: '发起授权失败',
        // detail 在 describeOAuthStartError 里用后端 message 兜底
        detail: '',
        cta: 'retry',
      };
  }
}

/** 从任意异常里取 code/message;非 ApiError 也兼容(测试与兜底包装会传普通 Error)。 */
function errorParts(error: unknown): { code: string; message: string } {
  if (error instanceof ApiError) {
    return { code: error.code || UNKNOWN_CODE, message: error.message };
  }
  if (error && typeof error === 'object') {
    const record = error as { code?: unknown; message?: unknown };
    return {
      code:
        typeof record.code === 'string' && record.code
          ? record.code
          : UNKNOWN_CODE,
      message: typeof record.message === 'string' ? record.message : '',
    };
  }
  if (typeof error === 'string') {
    return { code: UNKNOWN_CODE, message: error };
  }
  return { code: UNKNOWN_CODE, message: '' };
}

/**
 * 把 oauth/start 的异常翻译成面板文案。
 * 已知 code 用契约文案;未知 code 用后端 message 兜底,并保留原始 code 供排查。
 */
export function describeOAuthStartError(error: unknown): OAuthStartErrorInfo {
  const { code, message } = errorParts(error);
  const mapped = classify(code);
  const detail =
    mapped.kind === 'unknown'
      ? message.trim() ||
        '发起浏览器授权时出现未归类的错误，请稍后重试；若持续失败，请改用静态令牌或联系 server 维护者。'
      : mapped.detail;
  return { ...mapped, detail, code };
}
