/**
 * MCP 页(M1 本地版):用户自定义 MCP server 的注册表与连接定义。
 *
 * 与连接器页同一套定义式交互:左侧列表(右上 + 展开定义表单)、右侧详情;
 * - http:远程 Streamable HTTP server(URL/Token);
 * - stdio:本地进程(命令/参数/环境变量/运行时/镜像),由 orchestrator 沙箱拉起。
 * Token 与环境变量只写不读:读端只返回尾号与变量名,提交成功后清空敏感输入。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Globe,
  Plug,
  Plus,
  Power,
  RefreshCw,
  Terminal,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';

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
  Tabs,
  Textarea,
} from '@beauty/design';

import {
  ConsolePanel,
  EmptyPanel,
  Mono,
  PageHeader,
  ToneBadge,
} from '@/components/console/ui';
import type { Tone } from '@/lib/presentation';
import {
  isHttpUrl,
  isStdioCommand,
  mcpApi,
  parseArgsText,
  parseEnvText,
  type MCPServer,
  type MCPServerCreateInput,
  type MCPRuntime,
  type MCPTransport,
} from '@/lib/mcp-api';

const SERVERS_KEY = ['mcp-servers'];

const TRANSPORT_TABS = [
  { value: 'http', label: 'HTTP(远程)' },
  { value: 'stdio', label: 'stdio(本地进程)' },
];

type FormState = {
  name: string;
  transport: MCPTransport;
  url: string;
  token: string;
  command: string;
  argsText: string;
  envText: string;
  runtime: MCPRuntime;
  image: string;
};

const EMPTY_FORM: FormState = {
  name: '',
  transport: 'http',
  url: 'http://127.0.0.1:8010/mcp',
  token: '',
  command: '',
  argsText: '',
  envText: '',
  runtime: 'python',
  image: '',
};

function formatTime(value: string | null): string {
  if (!value) return '未测试';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

/** 列表副标题:http 给地址,stdio 给「运行时 命令 参数」,一眼看出连的是什么。 */
function describeEndpoint(server: MCPServer): string {
  if (server.transport === 'stdio') {
    return [server.runtime, server.command, ...(server.args ?? [])]
      .filter(Boolean)
      .join(' ');
  }
  return server.url;
}

/** 状态标签:停用优先于连接状态,免得上一次测试结果盖住「已停用」。 */
function serverStatus(server: MCPServer): { tone: Tone; label: string } {
  if (!server.enabled) return { tone: 'neutral', label: '已停用' };
  if (server.last_status === 'ok') return { tone: 'success', label: '已连接' };
  if (server.last_status === 'error')
    return { tone: 'danger', label: '连接失败' };
  return { tone: 'info', label: '待测试' };
}

function DefinitionRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[76px_1fr] items-start gap-2 py-[5px]">
      <dt className="text-[11px] leading-5 text-ha-faint">{label}</dt>
      <dd className="min-w-0 text-[12px] leading-5 text-ha-text-2">
        {children}
      </dd>
    </div>
  );
}

export function McpPage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    id: string;
    ok: boolean;
    detail: string;
  } | null>(null);

  const serversQuery = useQuery({
    queryKey: SERVERS_KEY,
    queryFn: mcpApi.list,
  });
  const servers = serversQuery.data ?? [];
  const selected =
    servers.find((server) => server.id === selectedId) ?? servers[0] ?? null;

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: SERVERS_KEY });

  const buildCreateInput = (): MCPServerCreateInput => {
    const trimmedName = form.name.trim();
    if (form.transport === 'stdio') {
      const trimmedImage = form.image.trim();
      return {
        transport: 'stdio',
        name: trimmedName,
        command: form.command.trim(),
        args: parseArgsText(form.argsText),
        env: parseEnvText(form.envText),
        runtime: form.runtime,
        ...(trimmedImage ? { image: trimmedImage } : {}),
      };
    }
    const trimmedToken = form.token.trim();
    return {
      transport: 'http',
      name: trimmedName,
      url: form.url.trim(),
      ...(trimmedToken ? { token: trimmedToken } : {}),
    };
  };

  const createMutation = useMutation({
    mutationFn: () => mcpApi.create(buildCreateInput()),
    onSuccess: async (server) => {
      setShowForm(false);
      // 环境变量与 Token 可能含密钥:提交成功后清空,不留在表单里
      setForm(EMPTY_FORM);
      setFormError(null);
      setSelectedId(server.id);
      await refresh();
    },
    onError: (failure) => setFormError((failure as Error).message),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: async (result, id) => {
      setTestResult({ id, ok: result.ok, detail: result.detail });
      setActionError(null);
      await refresh();
    },
    onError: (failure, id) => {
      setTestResult({ id, ok: false, detail: (failure as Error).message });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      mcpApi.update(input.id, { enabled: input.enabled }),
    onSuccess: async () => {
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mcpApi.remove(id),
    onSuccess: async (_data, id) => {
      setSelectedId((current) => (current === id ? null : current));
      setTestResult(null);
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const httpUrlError =
    form.transport === 'http' && form.url.trim() && !isHttpUrl(form.url)
      ? '需以 http:// 或 https:// 开头'
      : undefined;
  const commandError =
    form.transport === 'stdio' &&
    form.command.trim() &&
    !isStdioCommand(form.command)
      ? '只允许字母数字与 . _ / -,参数请填到下方参数框'
      : undefined;
  const canCreate =
    form.transport === 'http'
      ? Boolean(form.name.trim() && isHttpUrl(form.url))
      : Boolean(form.name.trim() && isStdioCommand(form.command));

  const updateForm = (patch: Partial<FormState>) => {
    setForm((current) => ({ ...current, ...patch }));
    setFormError(null);
  };

  const status = selected ? serverStatus(selected) : null;
  const result = selected && testResult?.id === selected.id ? testResult : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="MCP"
        subtitle="自定义 MCP server(远程 Streamable HTTP / 沙箱 stdio)· 工具以 mcp.<server>.<tool> 注入任务,Token 与环境变量只写不读"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <div className="mt-4 grid gap-4 lg:grid-cols-[320px_1fr]">
          <ConsolePanel className="p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold text-ha-text-2">
                我的 MCP
              </span>
              <button
                type="button"
                aria-label="添加 MCP server"
                onClick={() => setShowForm((value) => !value)}
                className="grid h-6 w-6 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>

            {showForm ? (
              <div className="mt-3 space-y-3 rounded-md border border-ha-border bg-ha-panel-2/60 p-3">
                <Field label="名称" htmlFor="mcp-name" required>
                  <Input
                    id="mcp-name"
                    value={form.name}
                    onChange={(event) =>
                      updateForm({ name: event.target.value })
                    }
                    placeholder="github"
                  />
                </Field>
                <div className="space-y-1.5">
                  <span className="text-[11px] font-medium text-ha-muted">
                    传输方式
                  </span>
                  <Tabs
                    size="sm"
                    block
                    value={form.transport}
                    onValueChange={(value) =>
                      updateForm({ transport: value as MCPTransport })
                    }
                    items={TRANSPORT_TABS}
                  />
                </div>

                {form.transport === 'http' ? (
                  <>
                    <Field
                      label="Server URL"
                      htmlFor="mcp-url"
                      required
                      error={httpUrlError}
                    >
                      <Input
                        id="mcp-url"
                        value={form.url}
                        onChange={(event) =>
                          updateForm({ url: event.target.value })
                        }
                        placeholder="https://…/mcp"
                      />
                    </Field>
                    <Field
                      label="Token(可选)"
                      htmlFor="mcp-token"
                      hint="保存在服务端,读端只回尾号"
                    >
                      <Input
                        id="mcp-token"
                        type="password"
                        value={form.token}
                        onChange={(event) =>
                          updateForm({ token: event.target.value })
                        }
                        placeholder="保存后只显示尾号"
                      />
                    </Field>
                  </>
                ) : (
                  <>
                    <Field
                      label="命令"
                      htmlFor="mcp-command"
                      required
                      hint="如 npx、uvx、python"
                      error={commandError}
                    >
                      <Input
                        id="mcp-command"
                        value={form.command}
                        onChange={(event) =>
                          updateForm({ command: event.target.value })
                        }
                        placeholder={form.runtime === 'node' ? 'npx' : 'uvx'}
                      />
                    </Field>
                    <div className="grid grid-cols-2 gap-2">
                      <Field label="运行时" htmlFor="mcp-runtime">
                        <Select
                          id="mcp-runtime"
                          value={form.runtime}
                          onChange={(event) =>
                            updateForm({
                              runtime: event.target.value as MCPRuntime,
                            })
                          }
                        >
                          <option value="python">Python</option>
                          <option value="node">Node</option>
                        </Select>
                      </Field>
                      <Field label="镜像(可选)" htmlFor="mcp-image">
                        <Input
                          id="mcp-image"
                          value={form.image}
                          onChange={(event) =>
                            updateForm({ image: event.target.value })
                          }
                          placeholder={
                            form.runtime === 'node'
                              ? 'node:22-slim'
                              : 'python:3.12-slim'
                          }
                        />
                      </Field>
                    </div>
                    <Field
                      label="参数(一行一个)"
                      htmlFor="mcp-args"
                      hint="如 -y、@modelcontextprotocol/server-filesystem"
                    >
                      <Textarea
                        id="mcp-args"
                        className="font-mono"
                        rows={3}
                        value={form.argsText}
                        onChange={(event) =>
                          updateForm({ argsText: event.target.value })
                        }
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
                        rows={3}
                        value={form.envText}
                        onChange={(event) =>
                          updateForm({ envText: event.target.value })
                        }
                        placeholder={'GITHUB_TOKEN=ghp_…\nLOG_LEVEL=debug'}
                      />
                    </Field>
                  </>
                )}

                {formError ? (
                  <p className="text-[12px] text-ha-danger">{formError}</p>
                ) : null}
                <Button
                  size="sm"
                  className="w-full"
                  disabled={!canCreate || createMutation.isPending}
                  onClick={() => createMutation.mutate()}
                >
                  <Plug className="mr-1 h-3.5 w-3.5" />
                  {createMutation.isPending ? '保存中…' : '注册 MCP server'}
                </Button>
              </div>
            ) : null}

            <div className="mt-3 space-y-1">
              {servers.length === 0 ? (
                <p className="px-1 py-3 text-[12px] leading-5 text-ha-dim">
                  还没有注册 MCP server。点右上角 + 添加:填远程 HTTP
                  地址,或由沙箱拉起的本地 stdio 进程。
                </p>
              ) : null}
              {servers.map((server) => {
                const tone = serverStatus(server);
                return (
                  <button
                    key={server.id}
                    type="button"
                    onClick={() => setSelectedId(server.id)}
                    className={
                      'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors ' +
                      (selected?.id === server.id
                        ? 'bg-[#1c1d1f]'
                        : 'hover:bg-ha-panel-2/60')
                    }
                  >
                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-ha-panel-2">
                      {server.transport === 'stdio' ? (
                        <Terminal className="h-3.5 w-3.5 text-ha-muted" />
                      ) : (
                        <Globe className="h-3.5 w-3.5 text-ha-muted" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-ha-text-2">
                        {server.name}
                      </span>
                      <span className="block truncate font-mono text-[11px] text-ha-dim">
                        {describeEndpoint(server)}
                      </span>
                    </span>
                    <ToneBadge tone={tone.tone}>{tone.label}</ToneBadge>
                  </button>
                );
              })}
            </div>
          </ConsolePanel>

          {selected && status ? (
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <CardTitle>{selected.name}</CardTitle>
                      <CardDescription>
                        {selected.transport === 'stdio'
                          ? 'stdio · 沙箱内本地进程'
                          : 'HTTP · 远程 Streamable HTTP'}{' '}
                        · 注册于 {formatTime(selected.created_at)}
                      </CardDescription>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={testMutation.isPending}
                        onClick={() => testMutation.mutate(selected.id)}
                      >
                        <RefreshCw className="mr-1 h-3 w-3" />
                        {testMutation.isPending ? '测试中…' : '测试/刷新'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={toggleMutation.isPending}
                        onClick={() =>
                          toggleMutation.mutate({
                            id: selected.id,
                            enabled: !selected.enabled,
                          })
                        }
                      >
                        <Power className="mr-1 h-3 w-3" />
                        {selected.enabled ? '停用' : '启用'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (
                            window.confirm(
                              '删除后该 server 的工具不再注入任务,确认删除?',
                            )
                          ) {
                            deleteMutation.mutate(selected.id);
                          }
                        }}
                      >
                        <Trash2 className="mr-1 h-3 w-3" />
                        删除
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <ToneBadge tone={status.tone}>{status.label}</ToneBadge>
                    <span className="font-mono text-[11px] text-ha-dim">
                      最近测试 {formatTime(selected.last_checked_at)}
                    </span>
                    {selected.last_status === 'ok' ? (
                      <Mono>{selected.tools.length} 个工具可用</Mono>
                    ) : null}
                  </div>

                  {selected.last_status === 'error' && selected.last_error ? (
                    <p className="text-[12px] text-ha-danger">
                      连接失败:{selected.last_error}
                    </p>
                  ) : null}
                  {result ? (
                    <p
                      className={
                        result.ok
                          ? 'text-[12px] text-ha-success'
                          : 'text-[12px] text-ha-danger'
                      }
                    >
                      测试{result.ok ? '通过' : '失败'}
                      {result.detail ? ':' + result.detail : ''}
                    </p>
                  ) : null}
                  {actionError ? (
                    <p className="text-[12px] text-ha-danger">{actionError}</p>
                  ) : null}

                  <dl className="rounded-md border border-ha-border bg-ha-panel-2/40 px-3 py-1.5">
                    <DefinitionRow label="传输方式">
                      <ToneBadge
                        tone={
                          selected.transport === 'stdio' ? 'info' : 'neutral'
                        }
                      >
                        {selected.transport === 'stdio' ? 'stdio' : 'HTTP'}
                      </ToneBadge>
                    </DefinitionRow>
                    {selected.transport === 'http' ? (
                      <>
                        <DefinitionRow label="地址">
                          <span className="break-all font-mono text-[11px] text-ha-muted">
                            {selected.url}
                          </span>
                        </DefinitionRow>
                        <DefinitionRow label="Token">
                          {selected.has_token ? (
                            <span className="font-mono text-[11px]">
                              ••••{selected.token_hint ?? ''}
                            </span>
                          ) : (
                            '未配置'
                          )}
                        </DefinitionRow>
                      </>
                    ) : (
                      <>
                        <DefinitionRow label="运行时">
                          {selected.runtime === 'node' ? 'Node' : 'Python'}
                        </DefinitionRow>
                        <DefinitionRow label="命令">
                          <span className="break-all font-mono text-[11px] text-ha-muted">
                            {selected.command}
                          </span>
                        </DefinitionRow>
                        <DefinitionRow label="参数">
                          {selected.args.length ? (
                            <span className="break-all font-mono text-[11px] text-ha-muted">
                              {selected.args.join(' ')}
                            </span>
                          ) : (
                            '无'
                          )}
                        </DefinitionRow>
                        <DefinitionRow label="镜像">
                          {selected.image ?? '按运行时取默认镜像'}
                        </DefinitionRow>
                        <DefinitionRow label="环境变量">
                          {selected.env_keys.length ? (
                            <span className="flex flex-wrap gap-1">
                              {selected.env_keys.map((key) => (
                                <span
                                  key={key}
                                  className="rounded border border-ha-border bg-ha-panel px-1.5 py-0.5 font-mono text-[10px] text-ha-muted"
                                >
                                  {key}
                                </span>
                              ))}
                            </span>
                          ) : (
                            '未配置'
                          )}
                        </DefinitionRow>
                      </>
                    )}
                    <DefinitionRow label="超时">
                      {selected.timeout_seconds} 秒
                    </DefinitionRow>
                  </dl>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>工具</CardTitle>
                  <CardDescription>
                    以 mcp.&lt;server&gt;.&lt;tool&gt;
                    注入任务,写操作默认需人工确认;点「测试/刷新」重新拉取。
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {selected.tools.length ? (
                    <ul className="space-y-1.5">
                      {selected.tools.map((tool) => {
                        const properties = tool.input_schema?.properties;
                        const paramCount =
                          properties && typeof properties === 'object'
                            ? Object.keys(properties).length
                            : 0;
                        return (
                          <li
                            key={tool.name}
                            className="rounded-md border border-ha-border bg-ha-panel-2/60 px-2.5 py-2"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <Mono className="truncate text-ha-text-2">
                                {tool.name}
                              </Mono>
                              <span className="shrink-0 font-mono text-[10px] text-ha-faint">
                                {paramCount} 个参数
                              </span>
                            </div>
                            {tool.description ? (
                              <p className="mt-0.5 text-[11px] leading-4 text-ha-dim">
                                {tool.description}
                              </p>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <p className="px-1 text-[12px] leading-5 text-ha-dim">
                      还没有拉取到工具。点右上角「测试/刷新」连接一次 server。
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>
          ) : (
            <EmptyPanel
              icon={<Plug className="h-6 w-6" />}
              title="还没有注册 MCP server"
              description="在左侧点 + 注册:填远程 Streamable HTTP server 的地址,或由沙箱拉起的本地 stdio 进程。注册后可测试、启停,并查看它暴露给任务的工具。"
            />
          )}
        </div>
      </div>
    </div>
  );
}
