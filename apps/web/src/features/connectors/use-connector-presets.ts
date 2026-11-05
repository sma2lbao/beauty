/**
 * /connectors 预设区的模型:预设目录 + 已建 MCP server 两条查询,
 * 「连接」= 幂等 ensure 预设 server → 发起浏览器授权 → 整页跳转;
 * 以及 /connectors?oauth=… 回调的一次性提示与 server 列表刷新。
 *
 * 与旧 connector-service 目录(use-connectors-model)互不依赖,只共用路由参数:
 * 旧回调不带 oauth,新回调只带 oauth,两边的 effect 各自识别、互不抢参。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import {
  describeOAuthStartError,
  type OAuthStartErrorInfo,
} from '@/features/mcp/oauth-start-error';
import { mcpApi, type MCPServer } from '@/lib/mcp-api';
import { queryKeys } from '@/lib/query-keys';
import { useNotificationsStore } from '@/stores/notifications-store';

import {
  CONNECTORS_RETURN_PATH,
  matchPresetServer,
  mcpPresetsApi,
  presetCardState,
  type McpPreset,
  type McpPresetCardState,
} from './mcp-presets';

/** 一张预设卡片的完整数据:预设 + 关联到的 server(可能还没创建)。 */
export type McpPresetCardData = {
  preset: McpPreset;
  server: MCPServer | null;
  state: McpPresetCardState;
};

/** 页面内提示条(授权回跳结果),与 toast 同时出现一次。 */
export type McpOauthNotice = {
  tone: 'success' | 'danger';
  title: string;
  description: string;
};

/** 某个预设发起授权失败时,错误只落在该卡片上。 */
export type McpPresetActionError = {
  presetKey: string;
  /** 一句化说明(兼容旧展示) */
  message: string;
  /** 按根因分类后的标题/说明/下一步,卡片面板直接消费 */
  info: OAuthStartErrorInfo;
  /** 失败时已解析到的 server id;有值才能点「改用令牌」 */
  serverId: string | null;
};

const OAUTH_ERROR_FALLBACK = '授权没有完成，请重新发起授权。';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** 回跳结果 → 页面提示条;不从 URL 派生的状态一律不放进来。 */
function buildOauthNotice(
  status: string | null,
  serverName: string | null,
  error: string | null,
): McpOauthNotice | null {
  if (status !== 'success' && status !== 'error') return null;
  if (status === 'success') {
    const label = serverName?.trim() || 'MCP 连接器';
    return {
      tone: 'success',
      title: 'MCP 连接器已连接',
      description: label + ' 授权完成，可以在会话里直接使用它的工具了。',
    };
  }
  return {
    tone: 'danger',
    title: 'MCP 连接器授权失败',
    description: error?.trim() || OAUTH_ERROR_FALLBACK,
  };
}

export function useConnectorPresets() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [actionError, setActionError] = useState<McpPresetActionError | null>(
    null,
  );
  // 「改用令牌」弹窗:目标预设与已解析到的 server;null 表示未打开
  const [tokenPresetKey, setTokenPresetKey] = useState<string | null>(null);
  const [tokenServerId, setTokenServerId] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  // connectMutation 里解析到的 server id:ensure 成功后 startOAuth 失败时,
  // onError 只有异常本身,靠这个 ref 把 id 带到「改用令牌」
  const lastConnectServerId = useRef<string | null>(null);
  // 首帧就从 URL 派生提示:effect 里只做副作用,清掉参数后提示仍留在页面上
  const [oauthNotice, setOauthNotice] = useState<McpOauthNotice | null>(() =>
    buildOauthNotice(
      searchParams.get('oauth'),
      searchParams.get('server'),
      searchParams.get('error'),
    ),
  );

  const presetsQuery = useQuery({
    queryKey: queryKeys.mcpPresets,
    queryFn: mcpPresetsApi.list,
  });
  const serversQuery = useQuery({
    queryKey: queryKeys.mcpServers,
    queryFn: mcpApi.list,
  });

  const cards = useMemo<McpPresetCardData[]>(() => {
    const servers = serversQuery.data ?? [];
    return (presetsQuery.data ?? []).map((preset) => {
      const server = matchPresetServer(preset, servers);
      return { preset, server, state: presetCardState(server) };
    });
  }, [presetsQuery.data, serversQuery.data]);

  const connectMutation = useMutation({
    mutationFn: async (input: { presetKey: string; serverId?: string }) => {
      // 已有 server 时直接重新授权;没有才幂等创建(同 url 不会重复建)。
      const serverId =
        input.serverId ?? (await mcpPresetsApi.ensure(input.presetKey)).id;
      lastConnectServerId.current = serverId;
      return mcpApi.startOAuth(serverId, CONNECTORS_RETURN_PATH);
    },
    onMutate: () => {
      lastConnectServerId.current = null;
      setActionError(null);
    },
    onSuccess: (result) => {
      // 第三方授权页只能整页跳转(SPA 路由跳不过去);jsdom 下用 hash 测。
      window.location.assign(result.authorize_url);
    },
    onError: (error, input) => {
      // 与 /mcp 页共用同一套根因分类,卡片给「原因标题 + 下一步」
      const info = describeOAuthStartError(error);
      setActionError({
        presetKey: input.presetKey,
        message: info.detail || info.title,
        info,
        serverId: lastConnectServerId.current,
      });
      // ensure 可能刚创建了 server:重拉一次,卡片才能拿到它并允许改用令牌
      void queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers });
    },
  });

  // 静态令牌保存:浏览器授权不可用时的替代路径
  const tokenMutation = useMutation({
    mutationFn: (input: { serverId: string; token: string }) =>
      mcpApi.update(input.serverId, { token: input.token }),
    onError: (error) =>
      setTokenError(errorMessage(error, '保存令牌失败，请稍后重试。')),
    onSuccess: async () => {
      setTokenError(null);
      setTokenPresetKey(null);
      setTokenServerId(null);
      setActionError(null);
      useNotificationsStore.getState().push({
        tone: 'success',
        title: '已保存静态令牌',
        description: '该连接器会改用令牌访问，不再走浏览器授权。',
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers });
    },
  });

  const openTokenDialog = (presetKey: string, serverId: string) => {
    setTokenError(null);
    setTokenPresetKey(presetKey);
    setTokenServerId(serverId);
  };
  const closeTokenDialog = () => {
    setTokenPresetKey(null);
    setTokenServerId(null);
    setTokenError(null);
  };
  const saveToken = (token: string) => {
    if (!tokenServerId) return;
    tokenMutation.mutate({ serverId: tokenServerId, token });
  };

  const connectingKey = connectMutation.isPending
    ? (connectMutation.variables?.presetKey ?? null)
    : null;

  const connectPreset = (preset: McpPreset, server: MCPServer | null) => {
    connectMutation.mutate({ presetKey: preset.key, serverId: server?.id });
  };

  // 授权回跳:/connectors?oauth=success&server=<name>
  // (失败 ?oauth=error&error=<msg>;state 验签失败时后端会降级回 /mcp,那里自己处理)。
  const oauthStatus = searchParams.get('oauth');
  const oauthServerName = searchParams.get('server');
  const oauthError = searchParams.get('error');
  useEffect(() => {
    const notice = buildOauthNotice(oauthStatus, oauthServerName, oauthError);
    if (!notice) return;
    // toast 进外部 store(不在这次渲染里 setState),提示条已由首帧 URL 派生
    useNotificationsStore.getState().push(notice);
    // 新授权结果写在 server 上:重新拉一次,卡片状态才是最新的
    void queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers });
    // 提示已经进 store,清掉参数避免刷新重复弹
    setSearchParams({}, { replace: true });
  }, [oauthStatus, oauthServerName, oauthError, setSearchParams, queryClient]);

  const refetch = () => {
    void presetsQuery.refetch();
    void serversQuery.refetch();
  };

  return {
    cards,
    loading: presetsQuery.isPending,
    error: presetsQuery.error,
    /** server 列表失败只影响卡片状态新鲜度,不挡预设目录 */
    serversError: serversQuery.error,
    refetch,
    connectingKey,
    actionError,
    clearActionError: () => setActionError(null),
    connectPreset,
    // 「改用令牌」弹窗(由页面渲染)
    tokenPresetKey,
    tokenSaving: tokenMutation.isPending,
    tokenError,
    openTokenDialog,
    closeTokenDialog,
    saveToken,
    oauthNotice,
    dismissOauthNotice: () => setOauthNotice(null),
  };
}
