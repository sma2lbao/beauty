/**
 * MCP 页(M1 本地版):用户自定义 MCP server 的注册表与连接定义。
 *
 * 定义式交互:左侧列表(右上 + 展开定义表单)、右侧详情;
 * - http:远程 Streamable HTTP server(URL/Token);
 * - stdio:本地进程(命令/参数/环境变量/运行时/镜像),由 orchestrator 沙箱拉起。
 * Token 与环境变量只写不读:读端只返回尾号与变量名,提交成功后清空敏感输入。
 * 「免确认执行」是每个 server 的信任开关:对话没有审批流(§6.7),只有开了它的
 * 工具才能在对话里直接被调用;关闭时工具仍可在「任务」路径按确认流程使用。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Globe,
  Plug,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Terminal,
  Trash2,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router';

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
  Switch,
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
  autoApprove: boolean;
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
  autoApprove: false,
};

function formatTime(value: string | null): string {
  if (!value) return '未测试';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

/** OAuth 令牌有效期展示:无过期时间与解析失败都给出可读文案。 */
function formatDeadline(value: string | null): string {
  if (!value) return '无固定期限';
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
  // 401 不是"连不上":服务端明确要求授权,提示用户去重新授权/换 token
  if (server.last_status === 'auth_required')
    return { tone: 'warning', label: '需要授权' };
  if (server.last_status === 'error')
    return { tone: 'danger', label: '连接失败' };
  return { tone: 'info', label: '待测试' };
}

/** 测试结果文案:401 单独提示"需要授权",不与普通连接失败混在一起。 */
function testResultText(result: {
  ok: boolean;
  authRequired: boolean;
}): string {
  if (result.authRequired) return '需要授权';
  return result.ok ? '测试通过' : '测试失败';
}

function testResultClass(result: {
  ok: boolean;
  authRequired: boolean;
}): string {
  if (result.authRequired) return 'text-[12px] text-ha-warning';
  return result.ok
    ? 'text-[12px] text-ha-success'
    : 'text-[12px] text-ha-danger';
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
  // 浏览器 OAuth 回调:BFF 把授权结果重定向回 /mcp?oauth=success&server=…|?oauth=error&error=…
  const [searchParams] = useSearchParams();
  const oauthStatus = searchParams.get('oauth');
  const oauthServerName = searchParams.get('server');
  const oauthErrorText = searchParams.get('error');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    id: string;
    ok: boolean;
    detail: string;
    authRequired: boolean;
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

  // 浏览器授权成功回调(带 ?oauth=success):该 server 的授权状态与 token 已变化,重新拉列表
  useEffect(() => {
    if (oauthStatus !== 'success') return;
    void queryClient.invalidateQueries({ queryKey: SERVERS_KEY });
  }, [oauthStatus, queryClient]);

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
        auto_approve: form.autoApprove,
        ...(trimmedImage ? { image: trimmedImage } : {}),
      };
    }
    const trimmedToken = form.token.trim();
    return {
      transport: 'http',
      name: trimmedName,
      url: form.url.trim(),
      auto_approve: form.autoApprove,
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
      setTestResult({
        id,
        ok: result.ok,
        detail: result.detail,
        authRequired: result.auth_required,
      });
      setActionError(null);
      await refresh();
    },
    onError: (failure, id) => {
      setTestResult({
        id,
        ok: false,
        detail: (failure as Error).message,
        authRequired: false,
      });
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

  const autoApproveMutation = useMutation({
    mutationFn: (input: { id: string; auto_approve: boolean }) =>
      mcpApi.update(input.id, { auto_approve: input.auto_approve }),
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

  const oauthStartMutation = useMutation({
    mutationFn: (id: string) => mcpApi.startOAuth(id),
    onSuccess: (result) => {
      setActionError(null);
      // 整页跳到授权服务器;授权完成后由授权服务器 → BFF → 本页 ?oauth=success|error
      window.location.href = result.authorize_url;
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const oauthDisconnectMutation = useMutation({
    mutationFn: (id: string) => mcpApi.disconnectOAuth(id),
    onSuccess: async () => {
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

  // 浏览器授权入口只给 http server,且必须是曾发起过授权(已动态注册 client)的那种
  const showOAuth = Boolean(
    selected && selected.transport !== 'stdio' && selected.has_oauth,
  );
  const oauthAuthorized = Boolean(selected?.oauth_authorized);
  // 未授权,或调用期被 401 打回(auth_required):用户需要重新走一遍浏览器授权
  const oauthNeedsAuthorize =
    !oauthAuthorized || selected?.last_status === 'auth_required';
  const oauthScopes = selected?.oauth_scopes ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="MCP"
        subtitle="自定义 MCP server(远程 Streamable HTTP / 沙箱 stdio)· 工具以 mcp.<server>.<tool> 注入任务,Token 与环境变量只写不读"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {oauthStatus === 'success' ? (
          <ConsolePanel className="mt-4 p-3">
            <p className="text-[12px] font-medium text-ha-success">
              {oauthServerName
                ? '浏览器授权成功:' + oauthServerName
                : '浏览器授权成功'}
            </p>
            <p className="mt-1 text-[11px] leading-4 text-ha-text-2">
              授权状态已刷新,可点「测试/刷新」重新拉取该 server
              的工具。
            </p>
          </ConsolePanel>
        ) : null}
        {oauthStatus === 'error' ? (
          <ConsolePanel className="mt-4 p-3 text-[12px] text-ha-danger">
            浏览器授权失败:{oauthErrorText || '未知错误'}
          </ConsolePanel>
        ) : null}

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

                <div className="flex items-start justify-between gap-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2">
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium text-ha-muted">
                      免确认执行
                    </p>
                    <p className="text-[10px] leading-4 text-ha-dim">
                      信任该 server:工具在对话里可直接执行,任务里也不再逐次确认
                    </p>
                  </div>
                  <Switch
                    aria-label="免确认执行(注册)"
                    checked={form.autoApprove}
                    onCheckedChange={(checked) =>
                      updateForm({ autoApprove: checked })
                    }
                  />
                </div>
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
                  {selected.last_status === 'auth_required' ? (
                    <div className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-[12px] leading-5">
                      <p className="font-medium text-ha-warning">
                        需要重新授权:该 server 拒绝了当前凭证(HTTP 401)
                      </p>
                      <p className="mt-1 text-ha-text-2">
                        {showOAuth
                          ? '点下方「去授权」在浏览器里重新授权后重新测试'
                          : '请更新该 server 的访问令牌后重新测试'}
                        {selected.last_error ? ':' + selected.last_error : ''}
                      </p>
                    </div>
                  ) : null}
                  {selected.oauth_error ? (
                    <div className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-[12px] leading-5">
                      <p className="font-medium text-ha-warning">
                        浏览器授权异常
                      </p>
                      <p className="mt-1 text-ha-text-2">
                        {selected.oauth_error}
                      </p>
                    </div>
                  ) : null}
                  {showOAuth ? (
                    <div className="rounded-md border border-ha-border bg-ha-panel-2/40 px-3 py-2">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-medium text-ha-text-2">
                              浏览器授权(OAuth)
                            </span>
                            <ToneBadge
                              tone={oauthAuthorized ? 'success' : 'neutral'}
                            >
                              {oauthAuthorized ? '已授权' : '未授权'}
                            </ToneBadge>
                          </div>
                          <p className="mt-1 text-[11px] leading-4 text-ha-dim">
                            {oauthAuthorized
                              ? '访问令牌有效期至 ' +
                                formatDeadline(selected.oauth_expires_at)
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
                            onClick={() =>
                              oauthStartMutation.mutate(selected.id)
                            }
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
                              onClick={() =>
                                oauthDisconnectMutation.mutate(selected.id)
                              }
                            >
                              <ShieldOff className="mr-1 h-3 w-3" />
                              {oauthDisconnectMutation.isPending
                                ? '解除中…'
                                : '解除授权'}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  ) : null}
                  {result ? (
                    <p className={testResultClass(result)}>
                      {testResultText(result)}
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
                    <DefinitionRow label="执行策略">
                      <span className="flex items-center gap-2">
                        <span>
                          {selected.auto_approve
                            ? '免确认执行 · 对话里可直接调用'
                            : '每次调用需确认 · 对话里只引导改用「任务」'}
                        </span>
                        <Switch
                          aria-label="免确认执行"
                          checked={selected.auto_approve}
                          disabled={autoApproveMutation.isPending}
                          onCheckedChange={(checked) =>
                            autoApproveMutation.mutate({
                              id: selected.id,
                              auto_approve: checked,
                            })
                          }
                        />
                      </span>
                    </DefinitionRow>
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
                    注入任务;开了「免确认执行」的工具在
                    对话里也能直接调用,其余仍需人工确认;点「测试/刷新」重新拉取。
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
