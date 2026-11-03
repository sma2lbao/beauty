/**
 * provider 卡片:未连接给授权入口,已连接给账号/状态与「去对话」「管理」,
 * 失效给「重新连接」。卡片只负责展示,动作经回调上抛。
 */
import { ArrowUpRight, Loader2, PlugZap, Wrench } from 'lucide-react';
import { Link } from 'react-router';

import {
  ConsolePanel,
  ConsoleButton,
  ToneBadge,
} from '@/components/console/ui';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  authKindLabel,
  CONNECTION_STATE_LABELS,
  CONNECTION_STATE_TONES,
  type ConnectorCardData,
} from './display';

export type ConnectorCardProps = {
  card: ConnectorCardData;
  connecting: boolean;
  onConnect: (provider: string) => void;
  onManage: (connectionId: string) => void;
};

/** 未连接时按钮不可用的原因(后端没配应用凭证)。 */
const NOT_CONFIGURED_HINT = '后端尚未配置该 provider 的应用凭证，暂不可连接。';

export function ConnectorCard({
  card,
  connecting,
  onConnect,
  onManage,
}: ConnectorCardProps) {
  const { provider, connection, state, extraConnections } = card;
  const notConfigured = provider.configured === false;
  const canChat = state === 'connected';
  const toolCount = provider.tools?.length ?? 0;
  const needsReconnect = state === 'reconnect';

  return (
    <ConsolePanel className="flex h-full flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-ha-border bg-ha-panel-2 text-ha-accent-soft">
            <PlugZap aria-hidden className="size-5" />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-medium text-ha-text">
              {provider.display_name}
            </h2>
            <p className="mt-0.5 truncate text-xs text-ha-muted">
              {authKindLabel(provider.auth_kind)} · {toolCount} 个工具
            </p>
          </div>
        </div>
        <ToneBadge tone={CONNECTION_STATE_TONES[state]}>
          {CONNECTION_STATE_LABELS[state]}
        </ToneBadge>
      </div>

      <p className="line-clamp-2 min-h-8 text-xs leading-5 text-ha-text-2">
        {provider.description}
      </p>

      {connection ? (
        <div className="space-y-1 text-xs text-ha-text-2">
          <p className="truncate">
            <span className="text-ha-muted">账号：</span>
            {connection.account || '未返回账号'}
          </p>
          {extraConnections > 0 ? (
            <p className="text-ha-muted">
              另有 {extraConnections} 条同 provider 的连接，可在「管理」中切换。
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-ha-muted">
          还未授权，连接后可在对话里调用它的工具。
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2">
        {state === 'none' || needsReconnect ? (
          <ConsoleButton
            variant="primary"
            size="sm"
            aria-label={
              needsReconnect
                ? `重新连接 ${provider.display_name}`
                : `连接 ${provider.display_name}`
            }
            disabled={connecting || notConfigured}
            title={notConfigured ? NOT_CONFIGURED_HINT : undefined}
            onClick={() => onConnect(provider.provider)}
          >
            {connecting ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <PlugZap aria-hidden className="size-3.5" />
            )}
            {needsReconnect ? '重新连接' : '连接'}
          </ConsoleButton>
        ) : null}

        {canChat ? (
          <Link
            to="/"
            className={`${consoleButtonClass({ variant: 'ghost', size: 'sm' })} gap-1`}
          >
            <ArrowUpRight aria-hidden className="size-3.5" />
            去对话
          </Link>
        ) : null}

        {connection ? (
          <ConsoleButton
            variant="secondary"
            size="sm"
            aria-label={`管理 ${provider.display_name}`}
            onClick={() => onManage(connection.id)}
          >
            <Wrench aria-hidden className="size-3.5" />
            管理
          </ConsoleButton>
        ) : null}

        {notConfigured && !connection ? (
          <span className="text-[11px] text-ha-dim">未配置应用凭证</span>
        ) : null}
      </div>
    </ConsolePanel>
  );
}
