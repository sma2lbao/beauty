/**
 * MCP 预设卡片:未创建 → 「连接」;已建未授权/授权失败 → 「授权」/「重新授权」;
 * 已连接 → 「去会话使用」(/?mcp=<server_id>)+「管理」(本页 #mcp-servers);
 * 已停用 → 提示到「我的 MCP 服务」启用。
 *
 * 卡片只展示与上抛动作,数据/跳转都在 use-connector-presets。
 */
import { ArrowUpRight, BookOpen, Loader2, PlugZap, Wrench } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import {
  ConsoleButton,
  ConsolePanel,
  ToneBadge,
} from '@/components/console/ui';
import type { Tone } from '@/lib/presentation';
import type { MCPServer } from '@/lib/mcp-api';

import type { McpPreset, McpPresetCardState } from './mcp-presets';

export type McpPresetCardProps = {
  preset: McpPreset;
  server: MCPServer | null;
  state: McpPresetCardState;
  connecting: boolean;
  /** 该预设上一次发起授权失败的原因;仅落在它自己的卡片上 */
  actionError: string | null;
  onConnect: () => void;
};

const STATE_LABELS: Record<McpPresetCardState, string> = {
  not_linked: '未连接',
  needs_auth: '待授权',
  connected: '已连接',
  disabled: '已停用',
};

const STATE_TONES: Record<McpPresetCardState, Tone> = {
  not_linked: 'neutral',
  needs_auth: 'warning',
  connected: 'success',
  disabled: 'neutral',
};

function bodyText(
  preset: McpPreset,
  server: MCPServer | null,
  state: McpPresetCardState,
): string {
  if (state === 'connected') {
    return '已连接 · ' + (server?.tools.length ?? 0) + ' 个工具';
  }
  if (state === 'disabled') {
    return '已在「我的 MCP 服务」停用，到该区启用后才能在会话里使用。';
  }
  if (state === 'needs_auth') {
    const reason = server?.oauth_error ?? server?.last_error;
    if (reason) return '上次授权没有成功：' + reason;
    if (server?.last_status === 'auth_required') {
      return '授权已失效，需要重新授权。';
    }
    return '还没有完成授权，完成后即可在会话里使用。';
  }
  return (
    '还没有创建连接，点「连接」会自动创建并跳到 ' + preset.name + ' 授权页。'
  );
}

export function McpPresetCard({
  preset,
  server,
  state,
  connecting,
  actionError,
  onConnect,
}: McpPresetCardProps) {
  const canConnect = state === 'not_linked' || state === 'needs_auth';
  const reauthorize = state === 'needs_auth';
  const failed = Boolean(server?.oauth_error);
  const tone = failed ? 'danger' : STATE_TONES[state];
  const label =
    failed && state === 'needs_auth' ? '授权失败' : STATE_LABELS[state];
  const buttonLabel = reauthorize ? '重新授权' : '连接';
  const ariaLabel = reauthorize
    ? '重新授权 ' + preset.name
    : '连接 ' + preset.name;

  return (
    <ConsolePanel className="flex h-full flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-ha-border bg-ha-panel-2 text-ha-accent-soft">
            <PlugZap aria-hidden className="size-5" />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-medium text-ha-text">
              {preset.name}
            </h2>
            <p className="mt-0.5 truncate text-xs text-ha-muted">
              MCP 预设 · {preset.url.replace(/^https?:\/\//, '')}
            </p>
          </div>
        </div>
        <ToneBadge tone={tone}>{label}</ToneBadge>
      </div>

      <p className="line-clamp-2 min-h-8 text-xs leading-5 text-ha-text-2">
        {preset.description}
      </p>

      <p className="text-xs text-ha-text-2">
        {bodyText(preset, server, state)}
      </p>

      {actionError ? (
        <p
          role="alert"
          className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-2 py-1.5 text-xs leading-5 text-ha-danger"
        >
          {actionError}
        </p>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center gap-2">
        {canConnect ? (
          <ConsoleButton
            variant="primary"
            size="sm"
            aria-label={ariaLabel}
            disabled={connecting}
            onClick={onConnect}
          >
            {connecting ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <PlugZap aria-hidden className="size-3.5" />
            )}
            {connecting ? '跳转中…' : buttonLabel}
          </ConsoleButton>
        ) : null}

        {state === 'connected' && server ? (
          <>
            <Link
              to={'/?mcp=' + encodeURIComponent(server.id)}
              aria-label={'去会话使用 ' + preset.name}
              className={`${consoleButtonClass({ variant: 'primary', size: 'sm' })} gap-1`}
            >
              <ArrowUpRight aria-hidden className="size-3.5" />
              去会话使用
            </Link>
            <Link
              to="/connectors#mcp-servers"
              aria-label={'管理 ' + preset.name}
              className={`${consoleButtonClass({ variant: 'secondary', size: 'sm' })} gap-1`}
            >
              <Wrench aria-hidden className="size-3.5" />
              管理
            </Link>
          </>
        ) : null}

        {state === 'disabled' ? (
          <Link
            to="/connectors#mcp-servers"
            aria-label={'管理 ' + preset.name}
            className={`${consoleButtonClass({ variant: 'secondary', size: 'sm' })} gap-1`}
          >
            <Wrench aria-hidden className="size-3.5" />
            到「我的 MCP 服务」启用
          </Link>
        ) : null}

        {preset.docs_url ? (
          <a
            href={preset.docs_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 px-1 text-xs text-ha-muted hover:text-ha-text-2"
          >
            <BookOpen aria-hidden className="size-3.5" />
            官方文档
          </a>
        ) : null}
      </div>
    </ConsolePanel>
  );
}
