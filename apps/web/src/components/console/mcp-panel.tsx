/**
 * MCP Client 设置面板(M1 本地版):注册第三方 MCP server、测试/刷新工具。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plug, RefreshCw, Trash2 } from 'lucide-react';
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

import { mcpApi } from '@/lib/mcp-api';

export function McpPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [url, setUrl] = useState('http://127.0.0.1:8010/mcp');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);

  const serversQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: mcpApi.list,
  });
  const servers = serversQuery.data ?? [];
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });

  const createMutation = useMutation({
    mutationFn: () =>
      mcpApi.create({
        name: name.trim(),
        url: url.trim(),
        token: token.trim() || undefined,
      }),
    onSuccess: async () => {
      setName('');
      setToken('');
      setError(null);
      await refresh();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: refresh,
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      mcpApi.update(input.id, { enabled: input.enabled }),
    onSuccess: refresh,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mcpApi.remove(id),
    onSuccess: refresh,
  });

  const canCreate = name.trim() && url.trim().startsWith('http');

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP Servers</CardTitle>
        <CardDescription>
          注册第三方 MCP server(Streamable HTTP)。工具以
          <span className="font-mono"> mcp.&lt;server&gt;.&lt;tool&gt; </span>
          命名空间注入任务,默认写操作需人工确认。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[160px_1fr_180px]">
          <Field label="名称" htmlFor="mcp-name">
            <Input
              id="mcp-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="github"
            />
          </Field>
          <Field label="Server URL" htmlFor="mcp-url">
            <Input
              id="mcp-url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://…/mcp"
            />
          </Field>
          <Field label="Token(可选)" htmlFor="mcp-token">
            <Input
              id="mcp-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="保存后只显示尾号"
            />
          </Field>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <Button
          size="sm"
          disabled={!canCreate || createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          <Plug className="mr-1 h-3.5 w-3.5" />
          {createMutation.isPending ? '保存中…' : '注册 MCP server'}
        </Button>

        {servers.length ? (
          <ul className="space-y-2">
            {servers.map((server) => (
              <li
                key={server.id}
                className="rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2.5"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-[13px] font-medium text-ha-text-2">
                      {server.name}
                      {server.has_token ? (
                        <span className="ml-2 font-mono text-[11px] text-ha-dim">
                          ••••{server.token_hint}
                        </span>
                      ) : null}
                    </p>
                    <p className="truncate font-mono text-[11px] text-ha-dim">
                      {server.url}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={testMutation.isPending}
                      onClick={() => testMutation.mutate(server.id)}
                    >
                      <RefreshCw className="mr-1 h-3 w-3" />
                      测试/刷新
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        toggleMutation.mutate({
                          id: server.id,
                          enabled: !server.enabled,
                        })
                      }
                    >
                      {server.enabled ? '停用' : '启用'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(server.id)}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ha-dim">
                  <span>
                    {server.last_status === 'ok'
                      ? `已连接 · ${server.tools.length} 个工具`
                      : server.last_status === 'error'
                        ? `连接失败:${server.last_error ?? ''}`
                        : '未测试'}
                  </span>
                  {server.tools.slice(0, 5).map((tool) => (
                    <span
                      key={tool.name}
                      className="rounded border border-ha-border bg-ha-panel px-1.5 py-0.5 font-mono text-ha-muted"
                    >
                      {tool.name}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            还没有注册 MCP server。本地可用 scripts/mock-mcp-server.py
            启动一个演示 server。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
