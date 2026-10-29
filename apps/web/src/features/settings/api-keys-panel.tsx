/**
 * 开放平台 API Key 面板(M3-01):创建/轮换/吊销,明文只显示一次。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, KeyRound, RotateCw, Trash2 } from 'lucide-react';
import { useState } from 'react';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
} from '@beauty/design';

import { apiKeysApi } from '@/lib/api-keys-api';
import { queryKeys } from '@/lib/query-keys';

const DEFAULT_SCOPES = ['mcp:tools:read', 'mcp:tools:call', 'artifacts:read'];

export function ApiKeysPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const keysQuery = useQuery({
    queryKey: queryKeys.apiKeys,
    queryFn: apiKeysApi.list,
  });
  const keys = keysQuery.data ?? [];
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.apiKeys });

  const createMutation = useMutation({
    mutationFn: () => apiKeysApi.create(name.trim(), DEFAULT_SCOPES),
    onSuccess: async (created) => {
      setName('');
      setNewKey(created.key);
      setError(null);
      await refresh();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.rotate(id),
    onSuccess: async (created) => {
      setNewKey(created.key);
      await refresh();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.revoke(id),
    onSuccess: refresh,
  });

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard?.writeText(key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 非安全上下文没有剪贴板权限:保留明文供手动复制
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>API Keys</CardTitle>
        <CardDescription>
          开放平台/MCP Server 凭证:只存哈希,明文仅在创建/轮换时显示一次; 默认
          scopes 为只读工具与 artifacts。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field
            label="名称"
            htmlFor="api-key-name"
            className="min-w-48 flex-1"
          >
            <Input
              id="api-key-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="本地 MCP 客户端"
            />
          </Field>
          <Button
            size="sm"
            disabled={!name.trim() || createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            <KeyRound className="mr-1 h-3.5 w-3.5" />
            {createMutation.isPending ? '生成中…' : '创建 API Key'}
          </Button>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {newKey ? (
          <div className="rounded-md border border-ha-warning-border bg-ha-warning-bg/40 p-3">
            <p className="text-[12px] font-medium text-ha-warning">
              新 Key 只显示这一次,请立即复制
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded border border-ha-border bg-ha-code px-2 py-1.5 font-mono text-[11px] text-ha-text-2">
                {newKey}
              </code>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void copyKey(newKey)}
              >
                <Copy className="mr-1 h-3 w-3" />
                {copied ? '已复制' : '复制'}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setNewKey(null)}
              >
                关闭
              </Button>
            </div>
          </div>
        ) : null}

        {keys.length ? (
          <ul className="space-y-2">
            {keys.map((key) => (
              <li
                key={key.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-ha-text-2">
                    {key.name}
                  </p>
                  <p className="font-mono text-[11px] text-ha-dim">
                    {key.masked_key} · {key.scopes.join(', ')}
                    {key.last_used_at ? ' · 最近使用' : ' · 未使用'}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={rotateMutation.isPending}
                    onClick={() => rotateMutation.mutate(key.id)}
                  >
                    <RotateCw className="mr-1 h-3 w-3" />
                    轮换
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={revokeMutation.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          '吊销后使用该 Key 的客户端会立即失败,确认?',
                        )
                      ) {
                        revokeMutation.mutate(key.id);
                      }
                    }}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">还没有 API Key。</p>
        )}
      </CardContent>
    </Card>
  );
}
