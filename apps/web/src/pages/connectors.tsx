/**
 * 连接器页 = MCP 预设连接器(主)+ connector-service provider 目录(旧,按需)。
 *
 * 主区:Notion 等内置 MCP 预设,一键「幂等创建 server → 浏览器 OAuth → 回跳」,
 * 已连接给「去会话使用」(/ ?mcp=<server_id>)与「管理」(/mcp)。
 * 旧目录区只在后端还返回 provider 时渲染(当前 connector-service 无内置 provider,
 * 即隐藏),组件与旧测试都保留,目录下线不影响本页。
 *
 * 页面只做组装:数据/动作分别在 use-connector-presets 与 use-connectors-model。
 * 安全边界:凭证永不出后端;授权一律整页跳第三方,本页不接触 access token。
 */
import { Cable, RefreshCw } from 'lucide-react';

import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  PageHeader,
} from '@/components/console/ui';
import { ConnectorCatalogSection } from '@/features/connectors/connector-catalog-section';
import { ConnectorDeleteDialog } from '@/features/connectors/connector-delete-dialog';
import { ConnectorDetailDrawer } from '@/features/connectors/connector-detail-drawer';
import { ConnectorTokenDialog } from '@/features/connectors/connector-token-dialog';
import { McpPresetCard } from '@/features/connectors/mcp-preset-card';
import { useConnectorPresets } from '@/features/connectors/use-connector-presets';
import { useConnectorsModel } from '@/features/connectors/use-connectors-model';

function noticeClass(tone: 'success' | 'danger'): string {
  return tone === 'success'
    ? 'border-ha-success-border bg-ha-success-bg text-ha-success'
    : 'border-ha-danger-border bg-ha-danger-bg text-ha-danger';
}

export function ConnectorsPage() {
  const presets = useConnectorPresets();
  const model = useConnectorsModel();
  const {
    selectedConnection,
    selectedProvider,
    detailLoading,
    closeDetail,
    notice,
    checkState,
    connect,
    tokenProvider,
    tokenConnecting,
    tokenConnectError,
    dismissTokenConnect,
    submitTokenConnect,
    togglingId,
    checkingId,
    refreshingId,
    removing,
    toggle,
    check,
    refresh,
    removal,
    requestRemoval,
    cancelRemoval,
    confirmRemoval,
  } = model;

  const tokenProviderName = tokenProvider
    ? (model.providers.find((item) => item.provider === tokenProvider)
        ?.display_name ?? tokenProvider)
    : '';

  const removalProviderName = removal
    ? (model.providers.find((item) => item.provider === removal.provider)
        ?.display_name ?? removal.provider)
    : '';

  const refreshAll = () => {
    presets.refetch();
    model.refetch();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="连接器"
        subtitle="Notion 等 MCP 预设一键授权 · 凭证只存在服务端，页面只管理连接与工具权限"
        actions={
          <ConsoleButton
            variant="secondary"
            size="sm"
            aria-label="刷新连接器"
            onClick={refreshAll}
          >
            <RefreshCw aria-hidden className="size-3.5" />
            刷新
          </ConsoleButton>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <section aria-labelledby="mcp-presets-heading" className="space-y-4">
          <div className="flex items-baseline justify-between gap-3">
            <h2
              id="mcp-presets-heading"
              className="text-sm font-medium text-ha-text"
            >
              MCP 预设连接器
            </h2>
            <p className="text-xs text-ha-dim">
              授权在第三方页面完成，完成即可在会话里调用
            </p>
          </div>

          {presets.oauthNotice ? (
            <div
              role="status"
              className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-xs leading-5 ${noticeClass(presets.oauthNotice.tone)}`}
            >
              <div>
                <p className="font-medium">{presets.oauthNotice.title}</p>
                <p>{presets.oauthNotice.description}</p>
              </div>
              <button
                type="button"
                onClick={presets.dismissOauthNotice}
                className="shrink-0 text-current/70 hover:text-current"
              >
                关闭
              </button>
            </div>
          ) : null}

          {presets.serversError ? (
            <p
              role="status"
              className="rounded-lg border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-xs leading-5 text-ha-warning"
            >
              MCP server 列表加载失败，卡片状态可能不是最新的：
              {presets.serversError instanceof Error
                ? presets.serversError.message
                : '未知错误'}
            </p>
          ) : null}

          {presets.loading ? (
            <div className="space-y-4">
              <p className="sr-only">正在加载 MCP 预设…</p>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {[0, 1, 2].map((key) => (
                  <ConsolePanel key={key} className="h-52 animate-pulse" />
                ))}
              </div>
            </div>
          ) : presets.error ? (
            <EmptyPanel
              icon={<Cable aria-hidden className="size-6" />}
              title="MCP 预设加载失败"
              description={
                presets.error instanceof Error
                  ? presets.error.message
                  : '请检查网络后重试。'
              }
              action={
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  onClick={presets.refetch}
                >
                  重试
                </ConsoleButton>
              }
            />
          ) : presets.cards.length === 0 ? (
            <EmptyPanel
              icon={<Cable aria-hidden className="size-6" />}
              title="暂无 MCP 预设"
              description="后端还没有内置 MCP 预设；上线后会出现在这里。"
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {presets.cards.map((card) => (
                <McpPresetCard
                  key={card.preset.key}
                  preset={card.preset}
                  server={card.server}
                  state={card.state}
                  connecting={presets.connectingKey === card.preset.key}
                  actionError={
                    presets.actionError?.presetKey === card.preset.key
                      ? presets.actionError.message
                      : null
                  }
                  onConnect={() =>
                    presets.connectPreset(card.preset, card.server)
                  }
                />
              ))}
            </div>
          )}

          <p className="text-xs leading-5 text-ha-muted">
            「连接」会按预设幂等创建一个 MCP server，再跳到第三方页面授权；
            授权完成后浏览器回到本页。凭证由服务端加密保存，本页不接触 access
            token。
          </p>
        </section>

        {model.providers.length > 0 ? (
          <section
            aria-labelledby="connector-catalog-heading"
            className="mt-8 space-y-4"
          >
            <h2
              id="connector-catalog-heading"
              className="text-sm font-medium text-ha-text"
            >
              其它连接器
            </h2>
            <ConnectorCatalogSection model={model} />
          </section>
        ) : null}
      </div>

      {selectedConnection ? (
        <ConnectorDetailDrawer
          connection={selectedConnection}
          provider={selectedProvider}
          loading={detailLoading}
          notice={notice}
          checkState={checkState}
          toggling={togglingId === selectedConnection.id}
          checking={checkingId === selectedConnection.id}
          refreshing={refreshingId === selectedConnection.id}
          onClose={closeDetail}
          onToggle={(enabled) => toggle(selectedConnection, enabled)}
          onCheck={() => check(selectedConnection.id)}
          onRefresh={() => refresh(selectedConnection.id)}
          onReconnect={() => connect(selectedConnection.provider)}
          onRemove={() => requestRemoval(selectedConnection)}
        />
      ) : null}

      <ConnectorTokenDialog
        open={tokenProvider !== null}
        providerName={tokenProviderName}
        pending={tokenConnecting}
        error={tokenConnectError}
        onSubmit={submitTokenConnect}
        onClose={dismissTokenConnect}
      />

      <ConnectorDeleteDialog
        connection={removal}
        providerName={removalProviderName}
        pending={removing}
        onCancel={cancelRemoval}
        onConfirm={confirmRemoval}
      />
    </div>
  );
}
