/**
 * OAuth 2.1 授权确认页(MCP 客户端用):登录后由用户确认 scopes。
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ShieldCheck } from 'lucide-react';
import { useSearchParams } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { Button } from '@beauty/design';
import { oauth21Api } from '@/lib/oauth21-api';

function denyRedirect(
  redirectUri: string,
  state: string | null,
): string | null {
  try {
    const url = new URL(redirectUri);
    url.searchParams.set('error', 'access_denied');
    if (state) url.searchParams.set('state', state);
    return url.toString();
  } catch {
    return null;
  }
}

export function OAuthAuthorizePage() {
  const [params] = useSearchParams();
  const clientId = params.get('client_id') ?? '';
  const redirectUri = params.get('redirect_uri') ?? '';
  const scope = params.get('scope') ?? '';
  const state = params.get('state');
  const challenge = params.get('code_challenge') ?? '';
  const method = params.get('code_challenge_method') ?? 'S256';
  const responseType = params.get('response_type') ?? 'code';

  const clientQuery = useQuery({
    queryKey: ['oauth-client', clientId],
    queryFn: () => oauth21Api.client(clientId),
    enabled: Boolean(clientId),
    retry: false,
  });

  const approveMutation = useMutation({
    mutationFn: () =>
      oauth21Api.approve({
        client_id: clientId,
        redirect_uri: redirectUri,
        scope,
        state,
        code_challenge: challenge,
        code_challenge_method: 'S256',
      }),
    onSuccess: (result) => {
      window.location.assign(result.redirect_to);
    },
  });

  const invalid =
    !clientId ||
    !redirectUri ||
    !challenge ||
    responseType !== 'code' ||
    method !== 'S256';
  const requestedScopes = scope.split(' ').filter(Boolean);

  return (
    <div className="grid min-h-svh place-items-center bg-ha-bg px-4 text-ha-text-body">
      <main className="w-full max-w-[460px] rounded-xl border border-ha-border-strong bg-ha-panel p-6">
        <BrandLockup size={28} />
        <h1 className="mt-5 text-[18px] font-semibold text-ha-text">
          授权 MCP 客户端
        </h1>
        <p className="mt-2 text-[13px] leading-6 text-ha-muted">
          {clientQuery.data?.client_name ||
            (clientQuery.isError ? '客户端不存在' : '读取客户端信息…')}
          请求访问你的租户数据。
        </p>

        {clientQuery.data ? (
          <div className="mt-5 space-y-3">
            <div>
              <p className="font-mono text-[11px] text-ha-faint">
                Redirect URI
              </p>
              <p className="mt-1 break-all rounded border border-ha-border bg-ha-code px-2 py-1.5 font-mono text-[11px] text-ha-muted">
                {redirectUri}
              </p>
            </div>
            <div>
              <p className="font-mono text-[11px] text-ha-faint">Scopes</p>
              <ul className="mt-1 space-y-1.5">
                {(requestedScopes.length
                  ? requestedScopes
                  : clientQuery.data.scopes
                ).map((item) => (
                  <li
                    key={item}
                    className="flex items-center gap-2 rounded border border-ha-border bg-ha-panel-2 px-2 py-1.5 font-mono text-[11px] text-ha-muted"
                  >
                    <ShieldCheck className="h-3.5 w-3.5 text-ha-success" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {invalid ? (
          <p className="mt-4 text-[12px] text-ha-danger">
            授权请求参数不完整或 PKCE 方法不受支持。
          </p>
        ) : null}
        {approveMutation.error ? (
          <p className="mt-4 text-[12px] text-ha-danger">
            {(approveMutation.error as Error).message}
          </p>
        ) : null}

        <div className="mt-6 flex items-center justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const target = denyRedirect(redirectUri, state);
              if (target) window.location.assign(target);
            }}
          >
            <ArrowLeft className="mr-1 h-3.5 w-3.5" />
            拒绝
          </Button>
          <Button
            size="sm"
            disabled={invalid || approveMutation.isPending}
            onClick={() => approveMutation.mutate()}
          >
            {approveMutation.isPending ? '授权中…' : '允许访问'}
          </Button>
        </div>
      </main>
    </div>
  );
}
