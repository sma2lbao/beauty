/**
 * 右列:选中 server 的详情卡(状态、错误/授权提示、连接定义)。
 */
import type { ReactNode } from 'react';
import { Power, RefreshCw, Trash2 } from 'lucide-react';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Switch,
} from '@beauty/design';

import { Mono, ToneBadge } from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';

import { testResultClass, testResultText } from './display';
import { OAuthAuthorizationBlock } from './oauth-authorization-block';
import type { McpPageModel } from './use-mcp-page-model';

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

export function ServerDetailCard({ model }: { model: McpPageModel }) {
  const {
    selected,
    status,
    result,
    actionError,
    showOAuth,
    testMutation,
    toggleMutation,
    deleteMutation,
    autoApproveMutation,
  } = model;
  if (!selected || !status) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{selected.name}</CardTitle>
            <CardDescription>
              {selected.transport === 'stdio'
                ? 'stdio · 沙箱内本地进程'
                : 'HTTP · 远程 Streamable HTTP'}{' '}
              · 注册于 {formatDateTime(selected.created_at, '未测试')}
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
            最近测试 {formatDateTime(selected.last_checked_at, '未测试')}
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
            <p className="font-medium text-ha-warning">浏览器授权异常</p>
            <p className="mt-1 text-ha-text-2">{selected.oauth_error}</p>
          </div>
        ) : null}
        {showOAuth ? <OAuthAuthorizationBlock model={model} /> : null}
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
              tone={selected.transport === 'stdio' ? 'info' : 'neutral'}
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
                  ? '免确认执行 · 会话里可直接调用'
                  : '每次调用需确认 · 会话里只引导改用「任务」'}
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
  );
}
