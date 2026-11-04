/**
 * 连接器页的数据与交互模型:provider 目录 + 用户连接两条查询、详情按需查询,
 * 以及连接/停用/检查/刷新/解除五类动作和 OAuth 回调提示。页面只做组装。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';

import {
  callbackErrorMessage,
  connectionName,
  buildConnectorCards,
} from '@/features/connectors/display';
import {
  checkOutcome,
  connectorApi,
  connectorsReturnUrl,
  type ConnectorConnectTokenInput,
  type ConnectorConnection,
} from '@/lib/connector-api';
import { queryKeys } from '@/lib/query-keys';
import { useNotificationsStore } from '@/stores/notifications-store';

import type { ConnectorTokenSubmit } from './connector-token-dialog';

/** 动作结果提示(抽屉/页头脚注用,不发 toast)。 */
export type ConnectorNotice = {
  tone: 'success' | 'danger';
  text: string;
};

/** 最近一次连通性检查的结果:失败且属于授权类错误时提示重新连接。 */
export type ConnectorCheckState = {
  connectionId: string;
  ok: boolean;
  detail: string | null;
  reconnect: boolean;
};

function failureText(failure: unknown, fallback: string): string {
  return failure instanceof Error && failure.message
    ? failure.message
    : fallback;
}

export function useConnectorsModel() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [removal, setRemoval] = useState<ConnectorConnection | null>(null);
  // 「粘贴令牌连接」弹窗针对的 provider key(打开即记住,提交时不再问)
  const [tokenProvider, setTokenProvider] = useState<string | null>(null);
  const [notice, setNotice] = useState<ConnectorNotice | null>(null);
  const [checkState, setCheckState] = useState<ConnectorCheckState | null>(
    null,
  );

  const providersQuery = useQuery({
    queryKey: queryKeys.connectorProviders,
    queryFn: connectorApi.listProviders,
  });
  const connectionsQuery = useQuery({
    queryKey: queryKeys.connectorConnections,
    queryFn: connectorApi.listConnections,
  });
  const providers = useMemo(
    () => providersQuery.data ?? [],
    [providersQuery.data],
  );
  const connections = useMemo(
    () => connectionsQuery.data ?? [],
    [connectionsQuery.data],
  );

  const detailQuery = useQuery({
    queryKey: queryKeys.connectorConnection(selectedId),
    queryFn: () => connectorApi.getConnection(selectedId ?? ''),
    enabled: selectedId !== null,
  });

  /** 卡片主连接可能被详情查询刷新得更新,抽屉优先用详情结果。 */
  const selectedConnection =
    detailQuery.data ??
    connections.find((item) => item.id === selectedId) ??
    null;
  const selectedProvider =
    providers.find((item) => item.provider === selectedConnection?.provider) ??
    null;

  const cards = useMemo(
    () => buildConnectorCards(providers, connections),
    [providers, connections],
  );

  const invalidateAll = useCallback(
    async (connectionId?: string | null) => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.connectorConnections,
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.connectorProviders,
      });
      if (connectionId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.connectorConnection(connectionId),
        });
      }
    },
    [queryClient],
  );

  // 浏览器 OAuth 回调:后端实现给 ?provider=..&status=connected&connection_id=<id>
  // (失败 ?status=error&code=..);任务卡/文档还有 ?connected=<id> / ?error=<code> 的旧形状。
  // 三种都认,避免后端实现差异;toast 一次后把参数清掉,刷新不会重复弹。
  const connectedId =
    searchParams.get('connection_id') ?? searchParams.get('connected');
  const callbackError = searchParams.get('error');
  const callbackStatus =
    searchParams.get('status') ??
    (connectedId ? 'connected' : callbackError ? 'error' : null);
  useEffect(() => {
    if (!callbackStatus) return;
    // 回调可能先于列表到达:等两条查询有结果(成功或失败)再弹,提示里才带得出名字
    if (providersQuery.isPending || connectionsQuery.isPending) return;
    const connection = connectedId
      ? connectionsQuery.data?.find((item) => item.id === connectedId)
      : undefined;
    const providerKey =
      searchParams.get('provider') ?? connection?.provider ?? '';
    const provider = providersQuery.data?.find(
      (item) => item.provider === providerKey,
    );
    const label =
      (connection ? connectionName(connection, provider) : '') ||
      provider?.display_name ||
      providerKey ||
      '连接器';
    const push = useNotificationsStore.getState().push;
    if (callbackStatus === 'connected') {
      push({
        tone: 'success',
        title: '连接器已连接',
        description: `${label} 授权完成,可以开始对话了。`,
      });
      // 新连接是回调里写入的,列表要重新拉一次
      void queryClient.invalidateQueries({
        queryKey: queryKeys.connectorConnections,
      });
    } else {
      push({
        tone: 'danger',
        title: '连接器连接失败',
        description: callbackErrorMessage(
          searchParams.get('code') ?? callbackError,
        ),
      });
    }
    setSearchParams({}, { replace: true });
  }, [
    callbackStatus,
    connectedId,
    callbackError,
    searchParams,
    setSearchParams,
    providersQuery.isPending,
    providersQuery.data,
    connectionsQuery.isPending,
    connectionsQuery.data,
    queryClient,
  ]);

  const authorizeMutation = useMutation({
    mutationFn: (provider: string) =>
      connectorApi.authorize(provider, {
        redirect_uri: connectorsReturnUrl(),
      }),
    onSuccess: (result) => {
      setNotice(null);
      // 整页跳到第三方授权页;完成后后端 302 回 /connectors?status=..
      window.location.assign(result.authorize_url);
    },
    onError: (failure) =>
      setNotice({
        tone: 'danger',
        text: failureText(failure, '发起授权失败,请稍后重试。'),
      }),
  });

  const tokenConnectMutation = useMutation({
    mutationFn: (input: ConnectorConnectTokenInput) =>
      connectorApi.connectWithToken(input),
    onSuccess: async (connection) => {
      setTokenProvider(null);
      setNotice({
        tone: 'success',
        text: `已连接 ${connectionName(connection)}。`,
      });
      useNotificationsStore.getState().push({
        tone: 'success',
        title: '连接器已连接',
        description: `${connectionName(connection)} 可以开始对话了。`,
      });
      await invalidateAll(connection.id);
    },
    // 失败保留弹窗:错误文案在弹窗里展示,便于用户改令牌重试
  });

  const updateMutation = useMutation({
    mutationFn: (input: {
      id: string;
      enabled?: boolean;
      display_name?: string;
    }) =>
      connectorApi.updateConnection(input.id, {
        enabled: input.enabled,
        display_name: input.display_name,
      }),
    onSuccess: async (connection, input) => {
      setNotice({
        tone: 'success',
        text:
          input.enabled === false
            ? `已停用 ${connectionName(connection)}。`
            : `已启用 ${connectionName(connection)}。`,
      });
      await invalidateAll(connection.id);
    },
    onError: (failure) =>
      setNotice({
        tone: 'danger',
        text: failureText(failure, '更新连接失败。'),
      }),
  });

  const checkMutation = useMutation({
    mutationFn: (id: string) => connectorApi.checkConnection(id),
    onSuccess: async (result, id) => {
      // 新契约返回连接对象(失败码写在 last_error_code),旧契约返回 {ok,...}
      const outcome = checkOutcome(result);
      setCheckState({
        connectionId: id,
        ok: outcome.ok,
        detail: outcome.detail,
        reconnect: outcome.reconnect,
      });
      await invalidateAll(id);
    },
    onError: (failure, id) =>
      setCheckState({
        connectionId: id,
        ok: false,
        detail: failureText(failure, '检查连通性失败。'),
        reconnect: false,
      }),
  });

  const refreshMutation = useMutation({
    mutationFn: (id: string) => connectorApi.refreshConnection(id),
    onSuccess: async (_result, id) => {
      setNotice({ tone: 'success', text: '凭证已刷新。' });
      setCheckState(null);
      await invalidateAll(id);
    },
    onError: (failure) =>
      setNotice({
        tone: 'danger',
        text: failureText(failure, '刷新凭证失败,可能仍需重新连接。'),
      }),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => connectorApi.removeConnection(id),
    onSuccess: async (_result, id) => {
      setRemoval(null);
      setSelectedId((current) => (current === id ? null : current));
      setCheckState(null);
      setNotice({ tone: 'success', text: '已解除连接,本地凭证已删除。' });
      await invalidateAll(id);
    },
    onError: (failure) =>
      setNotice({
        tone: 'danger',
        text: failureText(failure, '解除连接失败。'),
      }),
  });

  return {
    providers,
    connections,
    cards,
    loading: providersQuery.isPending || connectionsQuery.isPending,
    catalogError: providersQuery.error,
    connectionsError: connectionsQuery.error,
    refetch: () => {
      void providersQuery.refetch();
      void connectionsQuery.refetch();
    },

    selectedId,
    selectedConnection,
    selectedProvider,
    detailLoading: detailQuery.isPending && selectedId !== null,
    openDetail: (id: string) => {
      setNotice(null);
      setCheckState(null);
      setSelectedId(id);
    },
    closeDetail: () => setSelectedId(null),

    notice,
    dismissNotice: () => setNotice(null),
    checkState,

    // 授权进行中(卡片「连接/重新连接」按钮 loading)
    connectingProvider: authorizeMutation.isPending
      ? (authorizeMutation.variables ?? null)
      : null,
    connect: (provider: string) => {
      setNotice(null);
      authorizeMutation.mutate(provider);
    },

    // 粘贴令牌连接:令牌只在提交时随请求发送,不落任何缓存
    tokenProvider,
    tokenConnecting: tokenConnectMutation.isPending,
    tokenConnectError: tokenConnectMutation.isError
      ? failureText(tokenConnectMutation.error, '连接失败,请检查令牌后重试。')
      : null,
    openTokenConnect: (provider: string) => {
      setNotice(null);
      tokenConnectMutation.reset();
      setTokenProvider(provider);
    },
    dismissTokenConnect: () => {
      setTokenProvider(null);
      tokenConnectMutation.reset();
    },
    submitTokenConnect: (input: ConnectorTokenSubmit) => {
      if (!tokenProvider) return;
      tokenConnectMutation.mutate({ provider: tokenProvider, ...input });
    },

    // 行内动作的忙碌态:同一时刻只允许一个同类型动作
    togglingId: updateMutation.isPending
      ? (updateMutation.variables?.id ?? null)
      : null,
    checkingId: checkMutation.isPending
      ? (checkMutation.variables ?? null)
      : null,
    refreshingId: refreshMutation.isPending
      ? (refreshMutation.variables ?? null)
      : null,
    removing: removeMutation.isPending,
    toggle: (connection: ConnectorConnection, enabled: boolean) =>
      updateMutation.mutate({ id: connection.id, enabled }),
    check: (id: string) => checkMutation.mutate(id),
    refresh: (id: string) => refreshMutation.mutate(id),

    removal,
    requestRemoval: (connection: ConnectorConnection) => {
      setNotice(null);
      setRemoval(connection);
    },
    cancelRemoval: () => setRemoval(null),
    confirmRemoval: () => {
      if (removal) removeMutation.mutate(removal.id);
    },
  };
}

export type ConnectorsModel = ReturnType<typeof useConnectorsModel>;
