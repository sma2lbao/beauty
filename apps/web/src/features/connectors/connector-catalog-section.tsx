/**
 * connector-service 的直连 provider 目录区(GitHub 等)。
 *
 * 只在后端启用并返回 provider 时渲染(providers.length > 0);GitHub 卡片
 * 同时给出 GitHub App OAuth 与粘贴 PAT 两个入口,并提示 App 安装这一前置条件。
 * 页面只传模型,区块内部的加载/错误/空态与原来一致。
 */
import { Cable } from 'lucide-react';

import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
} from '@/components/console/ui';
import { ConnectorCard } from '@/features/connectors/connector-card';
import type { ConnectorsModel } from '@/features/connectors/use-connectors-model';

export function ConnectorCatalogSection({ model }: { model: ConnectorsModel }) {
  const {
    cards,
    loading,
    catalogError,
    connectionsError,
    refetch,
    connectingProvider,
    connect,
    openTokenConnect,
    openDetail,
  } = model;

  // 支持粘贴令牌的 provider 不需要应用凭证,不进「未配置」告警
  const unconfigured = model.providers.filter(
    (provider) =>
      provider.configured === false && provider.token_connectable !== true,
  );
  const github = model.providers.find(
    (provider) => provider.provider === 'github',
  );

  if (loading) {
    return (
      <div className="space-y-4">
        <p className="sr-only">正在加载连接器…</p>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((key) => (
            <ConsolePanel key={key} className="h-52 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (catalogError) {
    return (
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
    );
  }

  if (cards.length === 0) {
    return (
      <EmptyPanel
        icon={<Cable aria-hidden className="size-6" />}
        title="暂无可用连接器"
        description="后端暂未启用直连连接器；在 connector-service 配置 PROVIDERS_ENABLED 后会出现在这里。"
      />
    );
  }

  return (
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
                  <code>.env</code> 里填 <code>{envPrefix}_CLIENT_ID</code> 与{' '}
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
            onManage={openDetail}
          />
        ))}
      </div>

      {github ? (
        <div
          role="note"
          className="space-y-1 rounded-lg border border-ha-border bg-ha-panel-2 px-3 py-2 text-xs leading-5 text-ha-text-2"
        >
          <p className="font-medium text-ha-text">GitHub 连接说明</p>
          <p>
            {github.configured === false
              ? '「连接」走 GitHub App 授权，但服务端尚未配置 App 凭证；可先用「粘贴令牌连接」粘贴 PAT。'
              : '「连接」走 GitHub App 授权；「粘贴令牌连接」使用个人访问令牌（PAT），适合快速试用。'}
          </p>
          <p>
            授权后还需要把 GitHub App 安装到至少一个仓库，否则仓库、Issue 与 PR
            工具会因无权限失败。
          </p>
        </div>
      ) : null}

      <p className="text-xs leading-5 text-ha-muted">
        凭证经服务端加密保存，读取接口永不返回；授权在第三方页面完成，
        本页不接触 access token。授权失效或过期后需要重新连接。
      </p>
    </div>
  );
}
