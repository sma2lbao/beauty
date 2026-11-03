/**
 * 连接详情抽屉:账号/owner/授权方式/scope/工具清单/时间线/启用开关,
 * 以及检查连通性、刷新凭证、重新连接、解除连接。
 */
import { Switch } from '@beauty/design';
import { X } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';

import {
  ConsoleButton,
  Mono,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import { formatDateTime } from '@/lib/presentation';
import type {
  ConnectorConnection,
  ConnectorProvider,
} from '@/lib/connector-api';

import {
  authKindLabel,
  connectionName,
  connectionState,
  CONNECTION_STATE_LABELS,
  CONNECTION_STATE_TONES,
  connectionTools,
  missingScopes,
  TOOL_RISK_LABELS,
  TOOL_RISK_TONES,
  toolRisk,
} from './display';
import type {
  ConnectorCheckState,
  ConnectorNotice,
} from './use-connectors-model';

export type ConnectorDetailDrawerProps = {
  connection: ConnectorConnection;
  provider: ConnectorProvider | null;
  loading: boolean;
  notice: ConnectorNotice | null;
  checkState: ConnectorCheckState | null;
  toggling: boolean;
  checking: boolean;
  refreshing: boolean;
  onClose: () => void;
  onToggle: (enabled: boolean) => void;
  onCheck: () => void;
  onRefresh: () => void;
  onReconnect: () => void;
  onRemove: () => void;
};

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-xs">
      <span className="shrink-0 text-ha-muted">{label}</span>
      <span className="min-w-0 break-all text-right text-ha-text-2">
        {children}
      </span>
    </div>
  );
}

export function ConnectorDetailDrawer({
  connection,
  provider,
  loading,
  notice,
  checkState,
  toggling,
  checking,
  refreshing,
  onClose,
  onToggle,
  onCheck,
  onRefresh,
  onReconnect,
  onRemove,
}: ConnectorDetailDrawerProps) {
  // Esc 关闭:只挂监听,不动别的状态。
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const state = connectionState(connection);
  const name = connectionName(connection, provider);
  const tools = connectionTools(connection, provider);
  const missing = missingScopes(connection, provider);
  const granted = connection.scopes ?? [];
  const enabled = connection.enabled !== false;
  const checkForThis =
    checkState?.connectionId === connection.id ? checkState : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${name} 连接详情`}
      className="fixed inset-0 z-40 flex justify-end"
    >
      <button
        type="button"
        aria-label="关闭连接详情"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside className="relative flex h-full w-[520px] max-w-[94vw] flex-col border-l border-ha-border bg-ha-sidebar shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-ha-border px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-medium text-ha-text">
                {name}
              </h2>
              <ToneBadge tone={CONNECTION_STATE_TONES[state]}>
                {CONNECTION_STATE_LABELS[state]}
              </ToneBadge>
            </div>
            <p className="mt-1 truncate text-xs text-ha-muted">
              {provider?.display_name ?? connection.provider} ·{' '}
              {authKindLabel(connection.auth_kind ?? provider?.auth_kind)}
            </p>
          </div>
          <ConsoleButton
            variant="ghost"
            size="sm"
            aria-label="关闭"
            onClick={onClose}
          >
            <X aria-hidden className="size-4" />
          </ConsoleButton>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {notice ? (
            <p
              role="status"
              className={`rounded-lg border px-3 py-2 text-xs ${
                notice.tone === 'success'
                  ? 'border-ha-success-border bg-ha-success-bg text-ha-success'
                  : 'border-ha-danger-border bg-ha-danger-bg text-ha-danger'
              }`}
            >
              {notice.text}
            </p>
          ) : null}

          {checkForThis ? (
            <div
              role="status"
              className={`space-y-2 rounded-lg border px-3 py-2 text-xs ${
                checkForThis.ok
                  ? 'border-ha-success-border bg-ha-success-bg text-ha-success'
                  : 'border-ha-warning-border bg-ha-warning-bg text-ha-warning'
              }`}
            >
              <p className="flex items-center gap-2">
                <StatusDot tone={checkForThis.ok ? 'success' : 'warning'} />
                {checkForThis.ok ? '连通性正常' : '连通性检查失败'}
              </p>
              {checkForThis.detail ? (
                <p className="text-ha-text-2">{checkForThis.detail}</p>
              ) : null}
              {checkForThis.reconnect ? (
                <p className="text-ha-text-2">
                  授权已失效或缺少权限,请点「重新连接」重新授权。
                </p>
              ) : null}
            </div>
          ) : null}

          <section aria-label="连接信息" className="space-y-0.5">
            <h3 className="pb-1 text-xs font-medium text-ha-text">连接信息</h3>
            <DetailRow label="账号">
              <Mono>{connection.account || '—'}</Mono>
            </DetailRow>
            {connection.owner_subject ? (
              <DetailRow label="所有者">
                <Mono>{connection.owner_subject}</Mono>
              </DetailRow>
            ) : null}
            {connection.provider_account_id ? (
              <DetailRow label="提供方账号">
                <Mono>{connection.provider_account_id}</Mono>
              </DetailRow>
            ) : null}
            {connection.external_workspace ? (
              <DetailRow label="工作区">
                <Mono>{connection.external_workspace}</Mono>
              </DetailRow>
            ) : null}
            <DetailRow label="授权方式">
              {authKindLabel(connection.auth_kind ?? provider?.auth_kind)}
            </DetailRow>
            <DetailRow label="连接 ID">
              <Mono>{connection.id}</Mono>
            </DetailRow>
            {loading ? (
              <p className="pt-1 text-xs text-ha-muted">正在刷新连接详情…</p>
            ) : null}
          </section>

          <section aria-label="权限范围" className="space-y-2">
            <h3 className="text-xs font-medium text-ha-text">权限范围</h3>
            {missing.length > 0 ? (
              <p className="rounded-lg border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-xs text-ha-warning">
                缺少 {missing.length} 项授权权限：{missing.join('、')}
                ，需要重新连接并同意全部 scope。
              </p>
            ) : null}
            {granted.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {granted.map((scope) => (
                  <li key={scope}>
                    <ToneBadge tone="neutral" mono>
                      {scope}
                    </ToneBadge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ha-muted">
                后端未返回已授予的 scope。
              </p>
            )}
          </section>

          <section aria-label="工具清单" className="space-y-2">
            <h3 className="text-xs font-medium text-ha-text">
              工具清单（{tools.length}）
            </h3>
            {tools.length > 0 ? (
              <ul className="divide-y divide-ha-border rounded-lg border border-ha-border">
                {tools.map((tool) => {
                  const risk = toolRisk(tool.risk);
                  return (
                    <li key={tool.name} className="space-y-1 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <Mono className="text-xs text-ha-text">
                          {tool.name}
                        </Mono>
                        {risk ? (
                          <ToneBadge tone={TOOL_RISK_TONES[risk]}>
                            {TOOL_RISK_LABELS[risk]}
                          </ToneBadge>
                        ) : null}
                      </div>
                      <p className="text-xs leading-5 text-ha-text-2">
                        {tool.description}
                      </p>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-xs text-ha-muted">
                该连接没有任何可调用工具。
              </p>
            )}
          </section>

          <section aria-label="时间与状态" className="space-y-0.5">
            <h3 className="pb-1 text-xs font-medium text-ha-text">
              时间与状态
            </h3>
            <DetailRow label="凭证过期">
              {formatDateTime(connection.expires_at, '未返回')}
            </DetailRow>
            {connection.refresh_expires_at ? (
              <DetailRow label="刷新凭证到期">
                {formatDateTime(connection.refresh_expires_at, '未返回')}
              </DetailRow>
            ) : null}
            <DetailRow label="最近刷新">
              {formatDateTime(connection.last_refreshed_at, '尚未刷新')}
            </DetailRow>
            <DetailRow label="最近检查">
              {formatDateTime(connection.last_checked_at, '尚未检查')}
            </DetailRow>
            {connection.last_error_code ? (
              <DetailRow label="最近错误">
                <Mono className="text-ha-warning">
                  {connection.last_error_code}
                </Mono>
              </DetailRow>
            ) : null}
            <div className="flex items-center justify-between gap-4 pt-2">
              <span className="text-xs text-ha-muted">
                启用：关闭后会话不再加载该连接的工具
              </span>
              <Switch
                checked={enabled}
                aria-label="启用连接"
                disabled={toggling}
                onCheckedChange={onToggle}
              />
            </div>
          </section>
        </div>

        <footer className="flex flex-wrap gap-2 border-t border-ha-border px-5 py-3">
          <ConsoleButton
            variant="secondary"
            size="sm"
            disabled={checking}
            onClick={onCheck}
          >
            {checking ? '检查中…' : '检查连通性'}
          </ConsoleButton>
          <ConsoleButton
            variant="secondary"
            size="sm"
            disabled={refreshing}
            onClick={onRefresh}
          >
            {refreshing ? '刷新中…' : '刷新凭证'}
          </ConsoleButton>
          <ConsoleButton variant="primary" size="sm" onClick={onReconnect}>
            重新连接
          </ConsoleButton>
          <ConsoleButton
            variant="danger"
            size="sm"
            className="ml-auto"
            onClick={onRemove}
          >
            解除连接
          </ConsoleButton>
        </footer>
      </aside>
    </div>
  );
}
