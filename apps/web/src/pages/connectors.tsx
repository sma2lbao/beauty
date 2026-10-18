/**
 * 连接器页(M1 本地版):IMAP/SMTP 连接管理、最近邮件、发信。
 * 凭证只在创建时提交,读端永不返回;写操作(发信/撤销)二次确认。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Inbox,
  Mail,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Field,
  Input,
  Textarea,
} from '@beauty/design';

import { ConsolePanel, PageHeader, ToneBadge } from '@/components/console/ui';
import type { ConnectionInput } from '@/lib/connector-api';
import { connectorApi } from '@/lib/connector-api';

const EMPTY_FORM = {
  account: '',
  display_name: '',
  host: 'imap.qq.com',
  port: '993',
  username: '',
  password: '',
  smtp_host: '',
  smtp_port: '465',
};

function formatTime(value: string | null): string {
  if (!value) return '未测试';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

export function ConnectorsPage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const oauthStatus = searchParams.get('oauth');
  const oauthError = searchParams.get('oauth_error');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [sendForm, setSendForm] = useState({ to: '', subject: '', body: '' });
  const [sendResult, setSendResult] = useState<string | null>(null);

  const connectionsQuery = useQuery({
    queryKey: ['connector-connections'],
    queryFn: connectorApi.list,
  });
  const providersQuery = useQuery({
    queryKey: ['connector-oauth-providers'],
    queryFn: connectorApi.oauthProviders,
    retry: false,
  });
  const oauthMutation = useMutation({
    mutationFn: (provider: string) => connectorApi.startOAuth(provider),
    onSuccess: (result) => window.location.assign(result.authorize_url),
  });
  const connections = connectionsQuery.data ?? [];
  const selected =
    connections.find((item) => item.id === selectedId) ??
    connections[0] ??
    null;

  const emailsQuery = useQuery({
    queryKey: ['connector-emails', selected?.id],
    queryFn: () => connectorApi.emails(selected?.id as string),
    enabled: Boolean(selected?.id) && selected?.status === 'active',
    retry: false,
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['connector-connections'] });

  const createMutation = useMutation({
    mutationFn: (input: ConnectionInput) => connectorApi.create(input),
    onSuccess: async (connection) => {
      setShowForm(false);
      setForm(EMPTY_FORM);
      setFormError(null);
      setSelectedId(connection.id);
      await refresh();
    },
    onError: (failure) => setFormError((failure as Error).message),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => connectorApi.test(id),
    onSuccess: async () => {
      await refresh();
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => connectorApi.remove(id),
    onSuccess: refresh,
  });

  const sendMutation = useMutation({
    mutationFn: () => connectorApi.sendEmail(selected?.id as string, sendForm),
    onSuccess: (result) => {
      setSendResult(result.detail);
      setSendForm({ to: '', subject: '', body: '' });
    },
    onError: (failure) => setSendResult((failure as Error).message),
  });

  const submitConnection = () => {
    setFormError(null);
    createMutation.mutate({
      provider: 'imap',
      account: form.account.trim(),
      display_name: form.display_name.trim() || null,
      credentials: {
        host: form.host.trim(),
        port: Number(form.port) || 993,
        username: form.username.trim(),
        password: form.password,
        smtp_host: form.smtp_host.trim() || null,
        smtp_port: form.smtp_host.trim() ? Number(form.smtp_port) || 465 : null,
      },
    });
  };

  const canSubmit =
    form.account.trim() &&
    form.host.trim() &&
    form.username.trim() &&
    form.password.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="连接器"
        subtitle="邮箱连接(IMAP 收取 / SMTP 发信)· 凭证只在服务端加密保存,读端永不返回"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {oauthStatus === 'success' ? (
          <ConsolePanel className="mt-4 p-3 text-[12px] text-ha-success">
            OAuth 邮箱已连接,可测试/收取/发信。
          </ConsolePanel>
        ) : null}
        {oauthStatus === 'error' ? (
          <ConsolePanel className="mt-4 p-3 text-[12px] text-ha-danger">
            OAuth 授权失败:{oauthError || '未知错误'}
          </ConsolePanel>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-ha-faint">
            OAuth 邮箱
          </span>
          {(providersQuery.data ?? []).map((provider) => (
            <Button
              key={provider.provider}
              variant="outline"
              size="sm"
              disabled={!provider.enabled || oauthMutation.isPending}
              title={
                provider.enabled
                  ? '跳转 ' + provider.label + ' 授权页'
                  : '未配置 client id/secret'
              }
              onClick={() => oauthMutation.mutate(provider.provider)}
            >
              <Mail className="mr-1 h-3.5 w-3.5" />
              {provider.label}
              {!provider.enabled ? ' · 未配置' : ''}
            </Button>
          ))}
        </div>
        {oauthMutation.error ? (
          <p className="mt-2 text-[12px] text-ha-danger">
            {(oauthMutation.error as Error).message}
          </p>
        ) : null}

        <div className="mt-4 grid gap-4 lg:grid-cols-[320px_1fr]">
          <ConsolePanel className="p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold text-ha-text-2">
                我的连接
              </span>
              <button
                type="button"
                aria-label="添加连接"
                onClick={() => setShowForm((value) => !value)}
                className="grid h-6 w-6 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>

            {showForm ? (
              <div className="mt-3 space-y-3 rounded-md border border-ha-border bg-ha-panel-2/60 p-3">
                <div className="grid grid-cols-2 gap-2">
                  <Field label="邮箱" htmlFor="conn-account">
                    <Input
                      id="conn-account"
                      value={form.account}
                      onChange={(event) =>
                        setForm({ ...form, account: event.target.value })
                      }
                      placeholder="me@qq.com"
                    />
                  </Field>
                  <Field label="显示名" htmlFor="conn-name">
                    <Input
                      id="conn-name"
                      value={form.display_name}
                      onChange={(event) =>
                        setForm({ ...form, display_name: event.target.value })
                      }
                      placeholder="可选"
                    />
                  </Field>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="IMAP host" htmlFor="conn-host">
                    <Input
                      id="conn-host"
                      value={form.host}
                      onChange={(event) =>
                        setForm({ ...form, host: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="端口" htmlFor="conn-port">
                    <Input
                      id="conn-port"
                      value={form.port}
                      onChange={(event) =>
                        setForm({ ...form, port: event.target.value })
                      }
                    />
                  </Field>
                </div>
                <Field label="账号" htmlFor="conn-username">
                  <Input
                    id="conn-username"
                    value={form.username}
                    onChange={(event) =>
                      setForm({ ...form, username: event.target.value })
                    }
                    placeholder="通常与邮箱相同"
                  />
                </Field>
                <Field label="授权码 / 密码" htmlFor="conn-password">
                  <Input
                    id="conn-password"
                    type="password"
                    value={form.password}
                    onChange={(event) =>
                      setForm({ ...form, password: event.target.value })
                    }
                    placeholder="QQ 邮箱请使用授权码"
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="SMTP host(可选)" htmlFor="conn-smtp">
                    <Input
                      id="conn-smtp"
                      value={form.smtp_host}
                      onChange={(event) =>
                        setForm({ ...form, smtp_host: event.target.value })
                      }
                      placeholder="smtp.qq.com"
                    />
                  </Field>
                  <Field label="SMTP 端口" htmlFor="conn-smtp-port">
                    <Input
                      id="conn-smtp-port"
                      value={form.smtp_port}
                      onChange={(event) =>
                        setForm({ ...form, smtp_port: event.target.value })
                      }
                    />
                  </Field>
                </div>
                {formError ? (
                  <p className="text-[12px] text-ha-danger">{formError}</p>
                ) : null}
                <Button
                  size="sm"
                  className="w-full"
                  disabled={!canSubmit || createMutation.isPending}
                  onClick={submitConnection}
                >
                  {createMutation.isPending ? '保存中…' : '保存连接'}
                </Button>
              </div>
            ) : null}

            <div className="mt-3 space-y-1">
              {connections.length === 0 ? (
                <p className="px-1 py-3 text-[12px] leading-5 text-ha-dim">
                  还没有连接。点右上角 + 添加 IMAP 邮箱。
                </p>
              ) : null}
              {connections.map((connection) => (
                <button
                  key={connection.id}
                  type="button"
                  onClick={() => setSelectedId(connection.id)}
                  className={
                    'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors ' +
                    (selected?.id === connection.id
                      ? 'bg-[#1c1d1f]'
                      : 'hover:bg-ha-panel-2/60')
                  }
                >
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-ha-panel-2">
                    <Mail className="h-3.5 w-3.5 text-ha-muted" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] text-ha-text-2">
                      {connection.display_name || connection.account}
                    </span>
                    <span className="block truncate font-mono text-[11px] text-ha-dim">
                      {connection.account}
                    </span>
                  </span>
                  <ToneBadge
                    tone={
                      connection.status === 'active' ? 'success' : 'neutral'
                    }
                  >
                    {connection.status === 'active' ? '可用' : '已撤销'}
                  </ToneBadge>
                </button>
              ))}
            </div>
          </ConsolePanel>

          {selected ? (
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle>
                        {selected.display_name || selected.account}
                      </CardTitle>
                      <CardDescription>
                        {selected.account} · {selected.provider} · 最近测试{' '}
                        {formatTime(selected.last_checked_at)}
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
                        {testMutation.isPending ? '测试中…' : '测试'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={
                          selected.status !== 'active' ||
                          revokeMutation.isPending
                        }
                        onClick={() => {
                          if (
                            window.confirm(
                              '撤销后该连接不能再收发邮件,确认撤销?',
                            )
                          ) {
                            revokeMutation.mutate(selected.id);
                          }
                        }}
                      >
                        <Trash2 className="mr-1 h-3 w-3" />
                        撤销
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {testMutation.data &&
                  testMutation.variables === selected.id ? (
                    <p className="text-[12px] text-ha-muted">
                      {testMutation.data.ok ? '连通性正常:' : '测试失败:'}
                      {testMutation.data.detail}
                    </p>
                  ) : null}
                  {selected.status !== 'active' ? (
                    <p className="text-[12px] text-ha-dim">
                      连接已撤销,不能收取或发送邮件。
                    </p>
                  ) : null}
                </CardContent>
              </Card>

              <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>
                      <span className="inline-flex items-center gap-2">
                        <Inbox className="h-4 w-4" /> 最近邮件
                      </span>
                    </CardTitle>
                    <CardDescription>只读最近 10 封</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {emailsQuery.isPending ? (
                      <p className="text-sm text-muted-foreground">读取中…</p>
                    ) : emailsQuery.isError ? (
                      <p className="text-sm text-destructive">
                        {(emailsQuery.error as Error).message}
                      </p>
                    ) : emailsQuery.data?.length ? (
                      <ul className="space-y-2">
                        {emailsQuery.data.map((email, index) => (
                          <li
                            key={index}
                            className="rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2"
                          >
                            <p className="truncate text-[12px] font-medium text-ha-text-2">
                              {email.subject}
                            </p>
                            <p className="mt-1 truncate font-mono text-[11px] text-ha-dim">
                              {email.sender} · {email.sent_at}
                            </p>
                            {email.thread_id || email.has_attachments ? (
                              <p className="mt-1 flex flex-wrap gap-1.5 font-mono text-[10px] text-ha-faint">
                                {email.thread_id ? (
                                  <span className="rounded border border-ha-border px-1">
                                    线程 {email.thread_id.slice(0, 12)}
                                  </span>
                                ) : null}
                                {email.has_attachments ? (
                                  <span className="rounded border border-ha-border px-1">
                                    含附件
                                  </span>
                                ) : null}
                              </p>
                            ) : null}
                            <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-ha-muted">
                              {email.snippet}
                            </p>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <Empty>暂无邮件,或连接不可用。</Empty>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>
                      <span className="inline-flex items-center gap-2">
                        <Send className="h-4 w-4" /> 发送邮件
                      </span>
                    </CardTitle>
                    <CardDescription>
                      写操作:发送前会二次确认,需要 SMTP 配置
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Field label="收件人" htmlFor="send-to">
                      <Input
                        id="send-to"
                        value={sendForm.to}
                        onChange={(event) =>
                          setSendForm({ ...sendForm, to: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="主题" htmlFor="send-subject">
                      <Input
                        id="send-subject"
                        value={sendForm.subject}
                        onChange={(event) =>
                          setSendForm({
                            ...sendForm,
                            subject: event.target.value,
                          })
                        }
                      />
                    </Field>
                    <Field label="正文" htmlFor="send-body">
                      <Textarea
                        id="send-body"
                        className="min-h-28"
                        value={sendForm.body}
                        onChange={(event) =>
                          setSendForm({ ...sendForm, body: event.target.value })
                        }
                      />
                    </Field>
                    {sendResult ? (
                      <p className="flex items-center gap-1 text-[12px] text-ha-muted">
                        <ShieldCheck className="h-3.5 w-3.5" />
                        {sendResult}
                      </p>
                    ) : null}
                    <Button
                      size="sm"
                      disabled={
                        selected.status !== 'active' ||
                        sendMutation.isPending ||
                        !sendForm.to.trim() ||
                        !sendForm.subject.trim() ||
                        !sendForm.body.trim()
                      }
                      onClick={() => {
                        if (
                          window.confirm(
                            '确认发送这封邮件?该操作会产生真实副作用。',
                          )
                        ) {
                          setSendResult(null);
                          sendMutation.mutate();
                        }
                      }}
                    >
                      {sendMutation.isPending ? '发送中…' : '发送'}
                    </Button>
                  </CardContent>
                </Card>
              </div>
            </div>
          ) : (
            <ConsolePanel className="grid place-items-center p-10 text-[12px] text-ha-dim">
              从左侧选择一个连接,或添加新连接。
            </ConsolePanel>
          )}
        </div>
      </div>
    </div>
  );
}
