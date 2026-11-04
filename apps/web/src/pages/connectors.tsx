/**
 * 连接器页:按 provider 的卡片网格 + 连接详情抽屉。
 *
 * 页面只做组装:数据/动作都在 features/connectors 的模型里。
 * 安全边界:凭证永不出后端(列表/详情都不含 credentials),
 * 前端只持有 provider 目录与连接元数据;授权一律整页跳第三方。
 */
import { Cable, RefreshCw } from 'lucide-react';

import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  PageHeader,
} from '@/components/console/ui';
import { ConnectorCard } from '@/features/connectors/connector-card';
import { ConnectorDeleteDialog } from '@/features/connectors/connector-delete-dialog';
import { ConnectorDetailDrawer } from '@/features/connectors/connector-detail-drawer';
import { ConnectorTokenDialog } from '@/features/connectors/connector-token-dialog';
import { useConnectorsModel } from '@/features/connectors/use-connectors-model';

export function ConnectorsPage() {
  const model = useConnectorsModel();
  const {
    cards,
    loading,
    catalogError,
    connectionsError,
    refetch,
    selectedConnection,
    selectedProvider,
    detailLoading,
    closeDetail,
    notice,
    checkState,
    connectingProvider,
    connect,
    tokenProvider,
    tokenConnecting,
    tokenConnectError,
    openTokenConnect,
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

  // 支持粘贴令牌的 provider 不需要应用凭证,不进「未配置」告警
  const unconfigured = model.providers.filter(
    (provider) =>
      provider.configured === false && provider.token_connectable !== true,
  );

  const tokenProviderName = tokenProvider
    ? (model.providers.find((item) => item.provider === tokenProvider)
        ?.display_name ?? tokenProvider)
    : '';

  const removalProviderName = removal
    ? (model.providers.find((item) => item.provider === removal.provider)
        ?.display_name ?? removal.provider)
    : '';

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="连接器"
        subtitle="把第三方账号授权给 Agent · 凭证只存在服务端，页面只管理连接与工具权限"
        actions={
          <ConsoleButton
            variant="secondary"
            size="sm"
            aria-label="刷新连接器"
            onClick={refetch}
          >
            <RefreshCw aria-hidden className="size-3.5" />
            刷新
          </ConsoleButton>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {loading ? (
          <div className="space-y-4">
            <p className="sr-only">正在加载连接器…</p>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((key) => (
                <ConsolePanel key={key} className="h-52 animate-pulse" />
              ))}
            </div>
          </div>
        ) : catalogError ? (
          <EmptyPanel
            icon={<Cable aria-hidden className="size-6" />}
            title="连接器目录加载失败"
            description={
              catalogError instanceof Error
                ? catalogError.message
                : '请检查网络后重试。'
            }
            action={
              <ConsoleButton variant="secondary" size="sm" onClick={refetch}>
                重试
              </ConsoleButton>
            }
          />
        ) : cards.length === 0 ? (
          <EmptyPanel
            icon={<Cable aria-hidden className="size-6" />}
            title="暂无可用连接器"
            description="后端还没有内置 provider；后续上线后会出现在这里。"
          />
        ) : (
          <div className="space-y-4">
            {connectionsError ? (
              <p
                role="alert"
                className="rounded-lg border border-ha-danger-border bg-ha-danger-bg px-3 py-2 text-xs text-ha-danger"
              >
                连接列表加载失败，卡片状态可能不是最新的：
                {connectionsError instanceof Error
                  ? connectionsError.message
                  : '未知错误'}
              </p>
            ) : null}

            {unconfigured.length > 0 ? (
              <div
                role="status"
                className="rounded-lg border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-xs leading-5 text-ha-warning"
              >
                <p className="font-medium">
                  以下连接器还没配应用凭证，卡片上的「连接」按钮暂时不可用：
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {unconfigured.map((provider) => {
                    const envPrefix = `CONNECTOR__${provider.provider.toUpperCase()}`;
                    return (
                      <li key={provider.provider}>
                        {provider.display_name}：在 connector-service 的{' '}
                        <code>.env</code> 里填{' '}
                        <code>{envPrefix}_CLIENT_ID</code> 与{' '}
                        <code>{envPrefix}_CLIENT_SECRET</code>，重启服务后生效；
                        第三方后台登记的 Redirect URI 填{' '}
                        <code>
                          {window.location.origin}/api/connector/v1/oauth/
                          {provider.provider}/callback
                        </code>
                        。
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {cards.map((card) => (
                <ConnectorCard
                  key={card.provider.provider}
                  card={card}
                  connecting={connectingProvider === card.provider.provider}
                  onConnect={connect}
                  onConnectWithToken={openTokenConnect}
                  onManage={model.openDetail}
                />
              ))}
            </div>

            <p className="text-xs leading-5 text-ha-muted">
              凭证经服务端加密保存，读取接口永不返回；授权在第三方页面完成，
              本页不接触 access token。scope 变更或过期后需要重新连接。
            </p>
          </div>
        )}
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
