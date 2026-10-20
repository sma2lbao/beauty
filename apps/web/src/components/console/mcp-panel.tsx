/**
 * MCP Client 设置面板(M1 本地版):注册第三方 MCP server、测试/刷新工具。
 *
 * 支持两种传输方式:
 * - HTTP:远程 Streamable HTTP server(URL/Token);
 * - stdio:本地进程(命令/参数/环境变量/运行时/镜像),由 orchestrator 沙箱拉起。
 * 环境变量只写不读:列表只展示变量个数与名字,不回显值。
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
  Select,
  Tag,
  Tabs,
  Textarea,
} from '@beauty/design';

import {
  isHttpUrl,
  isStdioCommand,
  mcpApi,
  parseArgsText,
  parseEnvText,
  type MCPServerCreateInput,
  type MCPRuntime,
  type MCPTransport,
} from '@/lib/mcp-api';

const TRANSPORT_TABS = [
  { value: 'http', label: 'HTTP(远程)' },
  { value: 'stdio', label: 'stdio(本地进程)' },
];

type TestResult = { id: string; ok: boolean; detail: string };

export function McpPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [transport, setTransport] = useState<MCPTransport>('http');
  const [url, setUrl] = useState('http://127.0.0.1:8010/mcp');
  const [token, setToken] = useState('');
  const [command, setCommand] = useState('');
  const [argsText, setArgsText] = useState('');
  const [envText, setEnvText] = useState('');
  const [runtime, setRuntime] = useState<MCPRuntime>('python');
  const [image, setImage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const serversQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: mcpApi.list,
  });
  const servers = serversQuery.data ?? [];
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });

  const buildCreateInput = (): MCPServerCreateInput => {
    const trimmedName = name.trim();
    if (transport === 'stdio') {
      const trimmedImage = image.trim();
      return {
        transport: 'stdio',
        name: trimmedName,
        command: command.trim(),
        args: parseArgsText(argsText),
        env: parseEnvText(envText),
        runtime,
        ...(trimmedImage ? { image: trimmedImage } : {}),
      };
    }
    const trimmedToken = token.trim();
    return {
      transport: 'http',
      name: trimmedName,
      url: url.trim(),
      ...(trimmedToken ? { token: trimmedToken } : {}),
    };
  };

  const createMutation = useMutation({
    mutationFn: () => mcpApi.create(buildCreateInput()),
    onSuccess: async () => {
      setName('');
      setToken('');
      // 环境变量可能含密钥:提交成功后清空输入,避免留在表单里
      setEnvText('');
      setError(null);
      await refresh();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: async (result, id) => {
      setTestResult({ id, ok: result.ok, detail: result.detail });
      await refresh();
    },
    onError: (failure, id) =>
      setTestResult({ id, ok: false, detail: (failure as Error).message }),
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      mcpApi.update(input.id, { enabled: input.enabled }),
    onSuccess: refresh,
    onError: (failure) => setError((failure as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mcpApi.remove(id),
    onSuccess: refresh,
    onError: (failure) => setError((failure as Error).message),
  });

  const httpUrlError =
    transport === 'http' && url.trim() && !isHttpUrl(url)
      ? '需以 http:// 或 https:// 开头'
      : undefined;
  const commandError =
    transport === 'stdio' && command.trim() && !isStdioCommand(command)
      ? '只允许字母数字与 . _ / -,参数请填到下方参数框'
      : undefined;
  const canCreate =
    transport === 'http'
      ? Boolean(name.trim() && isHttpUrl(url))
      : Boolean(name.trim() && isStdioCommand(command));

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP Servers</CardTitle>
        <CardDescription>
          注册第三方 MCP server:远程 Streamable HTTP,或由沙箱拉起的本地 stdio
          进程。工具以
          <span className="font-mono"> mcp.&lt;server&gt;.&lt;tool&gt; </span>
          命名空间注入任务,默认写操作需人工确认。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
          <Field label="名称" htmlFor="mcp-name" required>
            <Input
              id="mcp-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="github"
            />
          </Field>
          <div className="flex flex-col justify-end gap-1.5">
            <span className="text-[11px] font-medium text-ha-muted">
              传输方式
            </span>
            <Tabs
              size="sm"
              value={transport}
              onValueChange={(value) => {
                setTransport(value as MCPTransport);
                setError(null);
              }}
              items={TRANSPORT_TABS}
            />
          </div>
        </div>

        {transport === 'http' ? (
          <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
            <Field
              label="Server URL"
              htmlFor="mcp-url"
              required
              error={httpUrlError}
            >
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
        ) : (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-[200px_150px_1fr]">
              <Field
                label="命令"
                htmlFor="mcp-command"
                required
                hint="如 npx、uvx、python"
                error={commandError}
              >
                <Input
                  id="mcp-command"
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  placeholder={runtime === 'node' ? 'npx' : 'uvx'}
                />
              </Field>
              <Field
                label="运行时"
                htmlFor="mcp-runtime"
                hint="决定默认沙箱镜像"
              >
                <Select
                  id="mcp-runtime"
                  value={runtime}
                  onChange={(event) =>
                    setRuntime(event.target.value as MCPRuntime)
                  }
                >
                  <option value="python">Python</option>
                  <option value="node">Node</option>
                </Select>
              </Field>
              <Field
                label="镜像(可选)"
                htmlFor="mcp-image"
                hint="留空按运行时选默认镜像"
              >
                <Input
                  id="mcp-image"
                  value={image}
                  onChange={(event) => setImage(event.target.value)}
                  placeholder={
                    runtime === 'node' ? 'node:22-slim' : 'python:3.12-slim'
                  }
                />
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="参数(一行一个)"
                htmlFor="mcp-args"
                hint="如 -y、@modelcontextprotocol/server-filesystem"
              >
                <Textarea
                  id="mcp-args"
                  className="font-mono"
                  rows={4}
                  value={argsText}
                  onChange={(event) => setArgsText(event.target.value)}
                  placeholder={
                    '-y\n@modelcontextprotocol/server-filesystem\n/tmp'
                  }
                />
              </Field>
              <Field
                label="环境变量(KEY=VALUE,一行一个)"
                htmlFor="mcp-env"
                hint="只写不读;列表只显示变量名,不回显值"
              >
                <Textarea
                  id="mcp-env"
                  className="font-mono"
                  rows={4}
                  value={envText}
                  onChange={(event) => setEnvText(event.target.value)}
                  placeholder={'GITHUB_TOKEN=ghp_…\nLOG_LEVEL=debug'}
                />
              </Field>
            </div>
          </div>
        )}

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
            {servers.map((server) => {
              const result = testResult?.id === server.id ? testResult : null;
              return (
                <li
                  key={server.id}
                  className="rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2.5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 text-[13px] font-medium text-ha-text-2">
                        <span className="truncate">{server.name}</span>
                        <Tag
                          color={
                            server.transport === 'stdio' ? 'info' : 'neutral'
                          }
                        >
                          {server.transport === 'stdio' ? 'stdio' : 'HTTP'}
                        </Tag>
                        {server.transport === 'http' && server.has_token ? (
                          <span className="shrink-0 font-mono text-[11px] text-ha-dim">
                            ••••{server.token_hint}
                          </span>
                        ) : null}
                      </p>
                      <p className="truncate font-mono text-[11px] text-ha-dim">
                        {server.transport === 'stdio'
                          ? [
                              server.runtime,
                              server.command,
                              ...(server.args ?? []),
                            ]
                              .filter(Boolean)
                              .join(' ')
                          : server.url}
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
                    {server.transport === 'stdio' ? (
                      <>
                        <span>
                          运行时 {server.runtime === 'node' ? 'Node' : 'Python'}
                        </span>
                        <span>
                          {server.image ? `镜像 ${server.image}` : '默认镜像'}
                        </span>
                        {server.has_env ? (
                          <span>
                            {server.env_keys.length
                              ? `已配置 ${server.env_keys.length} 个环境变量`
                              : '已配置环境变量'}
                          </span>
                        ) : null}
                      </>
                    ) : null}
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
                  {result ? (
                    <p
                      className={
                        result.ok
                          ? 'mt-1 text-[11px] text-ha-success'
                          : 'mt-1 text-[11px] text-destructive'
                      }
                    >
                      测试{result.ok ? '通过' : '失败'}
                      {result.detail ? `:${result.detail}` : ''}
                    </p>
                  ) : null}
                </li>
              );
            })}
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
