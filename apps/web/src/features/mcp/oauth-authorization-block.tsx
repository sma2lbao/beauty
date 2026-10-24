/**
 * 浏览器授权(OAuth)区块:授权状态、范围、授权/解除入口。
 */
import { ShieldCheck, ShieldOff } from 'lucide-react';

import { Button } from '@beauty/design';

import { ToneBadge } from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';

import type { McpPageModel } from './use-mcp-page-model';

export function OAuthAuthorizationBlock({
  model,
}: {
  model: McpPageModel;
}) {
  const {
    selected,
    oauthAuthorized,
    oauthNeedsAuthorize,
    oauthScopes,
    oauthStartMutation,
    oauthDisconnectMutation,
  } = model;
  if (!selected) return null;

  return (
    <div className="rounded-md border border-ha-border bg-ha-panel-2/40 px-3 py-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12px] font-medium text-ha-text-2">
              浏览器授权(OAuth)
            </span>
            <ToneBadge tone={oauthAuthorized ? 'success' : 'neutral'}>
              {oauthAuthorized ? '已授权' : '未授权'}
            </ToneBadge>
          </div>
          <p className="mt-1 text-[11px] leading-4 text-ha-dim">
            {oauthAuthorized
              ? '访问令牌有效期至 ' +
                formatDateTime(selected.oauth_expires_at, '无固定期限')
              : '授权在浏览器内完成,凭据只留在服务端,页面不回显 token'}
          </p>
          {oauthScopes.length ? (
            <p className="mt-1 flex flex-wrap items-center gap-1 font-mono text-[10px] text-ha-faint">
              范围
              {oauthScopes.map((scope) => (
                <span
                  key={scope}
                  className="rounded border border-ha-border bg-ha-panel px-1.5 py-0.5"
                >
                  {scope}
                </span>
              ))}
            </p>
          ) : null}
          {selected.oauth_authorization_server ? (
            <p className="mt-1 break-all font-mono text-[10px] text-ha-faint">
              授权服务器 {selected.oauth_authorization_server}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            size="sm"
            variant={oauthNeedsAuthorize ? 'primary' : 'outline'}
            disabled={oauthStartMutation.isPending}
            onClick={() => oauthStartMutation.mutate(selected.id)}
          >
            <ShieldCheck className="mr-1 h-3 w-3" />
            {oauthStartMutation.isPending
              ? '跳转中…'
              : oauthNeedsAuthorize
                ? '去授权'
                : '重新授权'}
          </Button>
          {oauthAuthorized ? (
            <Button
              variant="outline"
              size="sm"
              disabled={oauthDisconnectMutation.isPending}
              onClick={() => oauthDisconnectMutation.mutate(selected.id)}
            >
              <ShieldOff className="mr-1 h-3 w-3" />
              {oauthDisconnectMutation.isPending ? '解除中…' : '解除授权'}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
