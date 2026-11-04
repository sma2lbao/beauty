/**
 * 连接器授权恢复卡:工具因连接/授权问题失败时,就地给出「去连接器」入口。
 *
 * 覆盖可自助恢复的三类错误码:
 * - connector.connection_missing:本租户还没有可用连接(首次使用/已解绑);
 * - connector.auth_expired:refresh_token 失效或被撤销,需重新授权;
 * - connector.scope_missing:授权缺少工具所需 scope。
 *
 * C2.9 最小版:只做「跳转重连 + 回到会话重发」,不做 run 暂停续跑与自动重试;
 * 完整的中断-授权-续跑链路留待 C0.4 产品决策后进 P3。
 */
import { PlugZap, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { cn } from '@/lib/utils';

type RecoveryTone = 'warning' | 'danger';

type RecoveryCopy = {
  title: (label: string) => string;
  body: (label: string) => string;
  tone: RecoveryTone;
};

/** 错误码 → 文案;不在表里的错误码不渲染恢复卡。 */
const RECOVERY_COPY: Record<string, RecoveryCopy> = {
  'connector.connection_missing': {
    title: (label) => '尚未连接' + label,
    body: (label) =>
      '这次调用的工具需要 ' +
      label +
      ' 授权,当前还没有可用连接。连接后回到会话重新发送即可。',
    tone: 'warning',
  },
  'connector.auth_expired': {
    title: (label) => label + ' 连接已失效',
    body: (label) =>
      label +
      ' 的授权令牌已过期或被撤销,需要重新授权。重连后回到会话重新发送即可,本次任务不会自动继续。',
    tone: 'danger',
  },
  'connector.scope_missing': {
    title: (label) => label + ' 授权缺少权限',
    body: (label) =>
      '当前授权缺少该工具需要的 scope,重新授权并在' +
      label +
      '侧勾选对应权限后重试。',
    tone: 'warning',
  },
};

/** 工具全名形如 Provider__read_document,前缀即 provider key;取不到时返回 null。 */
export function connectorProviderFromToolName(toolName: string): string | null {
  const index = toolName.indexOf('__');
  return index > 0 ? toolName.slice(0, index).toLowerCase() : null;
}

/** 展示名只由 provider key 推导:首字母大写(demo → Demo);无 key 用通用「连接器」。 */
export function connectorProviderLabel(provider: string | null): string {
  if (!provider) return '连接器';
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

export function isConnectorRecoveryCode(
  errorCode: string | undefined,
): boolean {
  return errorCode !== undefined && errorCode in RECOVERY_COPY;
}

export function ConnectorRecoveryCard({
  errorCode,
  toolName,
}: {
  errorCode?: string;
  toolName: string;
}) {
  if (!errorCode || !(errorCode in RECOVERY_COPY)) {
    return null;
  }
  const copy = RECOVERY_COPY[errorCode];
  const provider = connectorProviderFromToolName(toolName);
  const label = connectorProviderLabel(provider);
  const danger = copy.tone === 'danger';
  const href = provider ? '/connectors?provider=' + provider : '/connectors';

  return (
    <div
      role="note"
      aria-label="连接器授权恢复"
      className={cn(
        'border-t px-3 py-2.5',
        danger
          ? 'border-ha-danger-border bg-ha-danger-bg'
          : 'border-ha-warning-border bg-ha-warning-bg',
      )}
    >
      <div className="flex items-start gap-2">
        <span
          className={cn(
            'mt-0.5 shrink-0',
            danger ? 'text-ha-danger' : 'text-ha-warning',
          )}
        >
          {danger ? <ShieldAlert size={14} /> : <PlugZap size={14} />}
        </span>
        <div className="min-w-0 space-y-1">
          <p
            className={cn(
              'text-[12px] font-medium',
              danger ? 'text-ha-danger' : 'text-ha-warning',
            )}
          >
            {copy.title(label)}
          </p>
          <p className="text-[11px] leading-[17px] text-ha-muted">
            {copy.body(label)}
          </p>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Link
              to={href}
              className={consoleButtonClass({
                variant: 'secondary',
                size: 'sm',
              })}
            >
              去连接器
            </Link>
            <span className="text-[11px] text-ha-faint">
              连接后回到会话重新发送
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
