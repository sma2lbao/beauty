/**
 * describeOAuthStartError:oauth/start 失败码 → 分类文案的逐 code 单测。
 *
 * 契约来自后端(oauth/start 的 502/409):前端只认 code,不依赖 details;
 * 这里把每个 code 的 kind/cta/标题钉住,避免文案漂移后用户又看到
 * 「不知道该改网络还是改令牌」的笼统错误。
 */
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/http';

import {
  describeOAuthStartError,
  type OAuthStartErrorInfo,
} from './oauth-start-error';

function apiError(code: string, message = '后端说明') {
  return new ApiError(502, code, message);
}

/** 期望映射表:code → [kind, cta, 标题] */
const EXPECTED: Array<
  [string, OAuthStartErrorInfo['kind'], OAuthStartErrorInfo['cta'], string]
> = [
  [
    'mcp.oauth_discovery_unreachable',
    'network',
    'retry',
    '无法连接 MCP server',
  ],
  [
    'mcp.oauth_authorization_server_unreachable',
    'network',
    'retry',
    '无法连接 MCP server',
  ],
  [
    'mcp.oauth_discovery_failed',
    'not_oauth',
    'token',
    '该 server 未启用 OAuth 授权',
  ],
  [
    'mcp.oauth_discovery_invalid',
    'bad_metadata',
    'token',
    'OAuth 元数据不合法',
  ],
  [
    'mcp.oauth_authorization_server_invalid',
    'bad_metadata',
    'token',
    'OAuth 元数据不合法',
  ],
  [
    'mcp.oauth_registration_unsupported',
    'no_dcr',
    'token',
    '授权服务器不支持动态注册',
  ],
  [
    'mcp.oauth_registration_failed',
    'registration_failed',
    'retry',
    'OAuth 动态注册失败',
  ],
  [
    'mcp.oauth_disabled',
    'server_config',
    'none',
    '该 server 的 OAuth 授权未启用',
  ],
  [
    'mcp.oauth_stdio_unsupported',
    'server_config',
    'none',
    '该传输方式不支持 OAuth',
  ],
];

describe('describeOAuthStartError', () => {
  it.each(EXPECTED)('%s → %s/%s:%s', (code, kind, cta, title) => {
    const info = describeOAuthStartError(apiError(code));
    expect(info.code).toBe(code);
    expect(info.kind).toBe(kind);
    expect(info.cta).toBe(cta);
    expect(info.title).toBe(title);
    // 每个分类都要给出具体下一步,不能是空文案
    expect(info.detail.length).toBeGreaterThan(10);
  });

  it('不再依赖 details:只有 code 也能正确分类', () => {
    // ApiError 只保留 status/code/message,没有任何 details 通道
    const info = describeOAuthStartError(
      apiError('mcp.oauth_registration_unsupported'),
    );
    expect(info.kind).toBe('no_dcr');
    expect(info.detail).toContain('静态令牌');
  });

  it('未知 code 用后端 message 兜底并保留 code', () => {
    const info = describeOAuthStartError(
      apiError('mcp.oauth_something_new', '授权服务器返回 503'),
    );
    expect(info.kind).toBe('unknown');
    expect(info.cta).toBe('retry');
    expect(info.detail).toBe('授权服务器返回 503');
    expect(info.code).toBe('mcp.oauth_something_new');
  });

  it('未知 code 且 message 为空时给通用兜底文案', () => {
    const info = describeOAuthStartError(
      apiError('mcp.oauth_something_new', ''),
    );
    expect(info.kind).toBe('unknown');
    expect(info.code).toBe('mcp.oauth_something_new');
    expect(info.detail).toContain('未归类');
  });

  it('普通 Error 也走兜底:没 code 记为 unknown,message 进 detail', () => {
    const info = describeOAuthStartError(new Error('network down'));
    expect(info.kind).toBe('unknown');
    expect(info.code).toBe('unknown');
    expect(info.detail).toBe('network down');
  });

  it('字符串/空值不抛异常', () => {
    expect(describeOAuthStartError('boom').detail).toBe('boom');
    expect(describeOAuthStartError(undefined).code).toBe('unknown');
    expect(describeOAuthStartError(null).kind).toBe('unknown');
  });
});
