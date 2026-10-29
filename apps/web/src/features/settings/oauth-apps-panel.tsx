/**
 * 已授权 MCP 应用面板:查看/撤销当前租户的 OAuth 授权。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldOff } from 'lucide-react';
import { useState } from 'react';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@beauty/design';

import { oauth21Api } from '@/lib/oauth21-api';
import { queryKeys } from '@/lib/query-keys';

export function OAuthAppsPanel() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const appsQuery = useQuery({
    queryKey: queryKeys.oauthAuthorizations,
    queryFn: oauth21Api.authorizations,
  });
  const apps = appsQuery.data ?? [];
  const revokeMutation = useMutation({
    mutationFn: (clientId: string) => oauth21Api.revokeAuthorization(clientId),
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.oauthAuthorizations,
      });
    },
    onError: (failure) => setError((failure as Error).message),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>已授权应用</CardTitle>
        <CardDescription>
          通过 OAuth 2.1 授权访问 MCP Server 的客户端;撤销后其 access/refresh
          token 立即失效。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {apps.length ? (
          <ul className="space-y-2">
            {apps.map((app) => (
              <li
                key={app.client_id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-ha-text-2">
                    {app.client_name}
                  </p>
                  <p className="truncate font-mono text-[11px] text-ha-dim">
                    {app.client_id} · {app.scopes.join(', ')}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={revokeMutation.isPending}
                  onClick={() => {
                    if (window.confirm('撤销后该应用需要重新授权,确认?')) {
                      revokeMutation.mutate(app.client_id);
                    }
                  }}
                >
                  <ShieldOff className="mr-1 h-3 w-3" />
                  撤销
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            还没有 OAuth 应用授权。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
