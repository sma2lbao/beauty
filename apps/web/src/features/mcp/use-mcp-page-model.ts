/**
 * MCP 管理区的数据与交互模型:server 注册表查询、注册/测试/启停/删除/授权变更,
 * 以及 OAuth 回跳参数与表单状态。页面只负责布局与组合。
 *
 * options.returnPath:浏览器授权完成后回到哪个前端页面。独立 /mcp 页不传
 * (后端默认回 /mcp);嵌在「连接器」页时传 /connectors。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { mcpApi } from '@/lib/mcp-api';
import { queryKeys } from '@/lib/query-keys';

import { serverStatus } from './display';
import {
  describeOAuthStartError,
  type OAuthStartErrorInfo,
} from './oauth-start-error';
import {
  buildCreateInput,
  canCreateFor,
  commandErrorFor,
  EMPTY_FORM,
  httpUrlErrorFor,
  type FormState,
} from './form';

export type UseMcpPageModelOptions = {
  /** 授权完成后回到的前端路径;缺省由后端回 /mcp */
  returnPath?: string;
};

export function useMcpPageModel(options: UseMcpPageModelOptions = {}) {
  const { returnPath } = options;
  const queryClient = useQueryClient();
  // 浏览器 OAuth 回调:API 网关把授权结果重定向回发起页
  // (默认 /mcp,连接器页为 /connectors)?oauth=success&server=…|?oauth=error&error=…
  const [searchParams] = useSearchParams();
  const oauthStatus = searchParams.get('oauth');
  const oauthServerName = searchParams.get('server');
  const oauthErrorText = searchParams.get('error');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // oauth/start 失败按根因分类,保留 ApiError 里的 code 而不只是 message;
  // 只对发起它的 server 展示(id 不同视为已过期,不串到别的 server 上)
  const [oauthStartFailure, setOauthStartFailure] = useState<{
    id: string;
    info: OAuthStartErrorInfo;
  } | null>(null);
  // 「改用令牌」弹窗目标:null 表示未打开;保存走 PATCH {token}
  const [tokenTargetId, setTokenTargetId] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    id: string;
    ok: boolean;
    detail: string;
    authRequired: boolean;
  } | null>(null);

  const serversQuery = useQuery({
    queryKey: queryKeys.mcpServers,
    queryFn: mcpApi.list,
  });
  const servers = serversQuery.data ?? [];
  const selected =
    servers.find((server) => server.id === selectedId) ?? servers[0] ?? null;

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers });

  // 浏览器授权成功回调(带 ?oauth=success):该 server 的授权状态与 token 已变化,重新拉列表
  useEffect(() => {
    if (oauthStatus !== 'success') return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers });
  }, [oauthStatus, queryClient]);

  const createMutation = useMutation({
    mutationFn: () => mcpApi.create(buildCreateInput(form)),
    onSuccess: async (server) => {
      setShowForm(false);
      // 环境变量与 Token 可能含密钥:提交成功后清空,不留在表单里
      setForm(EMPTY_FORM);
      setFormError(null);
      setSelectedId(server.id);
      await refresh();
    },
    onError: (failure) => setFormError((failure as Error).message),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => mcpApi.test(id),
    onSuccess: async (result, id) => {
      setTestResult({
        id,
        ok: result.ok,
        detail: result.detail,
        authRequired: result.auth_required,
      });
      setActionError(null);
      await refresh();
    },
    onError: (failure, id) => {
      setTestResult({
        id,
        ok: false,
        detail: (failure as Error).message,
        authRequired: false,
      });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      mcpApi.update(input.id, { enabled: input.enabled }),
    onSuccess: async () => {
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const autoApproveMutation = useMutation({
    mutationFn: (input: { id: string; auto_approve: boolean }) =>
      mcpApi.update(input.id, { auto_approve: input.auto_approve }),
    onSuccess: async () => {
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mcpApi.remove(id),
    onSuccess: async (_data, id) => {
      setSelectedId((current) => (current === id ? null : current));
      setTestResult(null);
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const oauthStartMutation = useMutation({
    mutationFn: (id: string) =>
      returnPath ? mcpApi.startOAuth(id, returnPath) : mcpApi.startOAuth(id),
    onMutate: () => {
      // 重试/重新发起时先清掉上一次的分类结果
      setOauthStartFailure(null);
      setActionError(null);
    },
    onSuccess: (result) => {
      setActionError(null);
      // 整页跳到授权服务器;授权完成后由授权服务器 → API 网关 → 发起页 ?oauth=success|error
      window.location.href = result.authorize_url;
    },
    // 失败保留 ApiError:分类需要 code,只展示 message 会让用户看不出下一步
    onError: (failure, id) =>
      setOauthStartFailure({ id, info: describeOAuthStartError(failure) }),
  });

  // 静态令牌保存:浏览器授权走不通时的替代路径
  const tokenMutation = useMutation({
    mutationFn: (input: { id: string; token: string }) =>
      mcpApi.update(input.id, { token: input.token }),
    onSuccess: async () => {
      setTokenTargetId(null);
      setTokenError(null);
      setOauthStartFailure(null);
      await refresh();
    },
    onError: (failure) => setTokenError((failure as Error).message),
  });

  const oauthDisconnectMutation = useMutation({
    mutationFn: (id: string) => mcpApi.disconnectOAuth(id),
    onSuccess: async () => {
      setActionError(null);
      await refresh();
    },
    onError: (failure) => setActionError((failure as Error).message),
  });

  const httpUrlError = httpUrlErrorFor(form);
  const commandError = commandErrorFor(form);
  const canCreate = canCreateFor(form);

  const updateForm = (patch: Partial<FormState>) => {
    setForm((current) => ({ ...current, ...patch }));
    setFormError(null);
  };

  const status = selected ? serverStatus(selected) : null;
  const result = selected && testResult?.id === selected.id ? testResult : null;

  // 浏览器授权入口只给 http server,且必须是曾发起过授权(已动态注册 client)的那种
  const showOAuth = Boolean(
    selected && selected.transport !== 'stdio' && selected.has_oauth,
  );
  const oauthAuthorized = Boolean(selected?.oauth_authorized);
  // 未授权,或调用期被 401 打回(auth_required):用户需要重新走一遍浏览器授权
  const oauthNeedsAuthorize =
    !oauthAuthorized || selected?.last_status === 'auth_required';
  const oauthScopes = selected?.oauth_scopes ?? [];
  // 分类结果按当前选中 server 派生:切到别的 server 时旧错误自然不展示
  const oauthStartError =
    selected && oauthStartFailure?.id === selected.id
      ? oauthStartFailure.info
      : null;
  const tokenDialogOpen = tokenTargetId !== null;

  /** 重试当前 server 的浏览器授权 */
  const retryOAuth = () => {
    if (selected) oauthStartMutation.mutate(selected.id);
  };
  const openTokenDialog = () => {
    if (!selected) return;
    setTokenError(null);
    setTokenTargetId(selected.id);
  };
  const closeTokenDialog = () => {
    setTokenTargetId(null);
    setTokenError(null);
  };
  const saveToken = (token: string) => {
    if (!tokenTargetId) return;
    tokenMutation.mutate({ id: tokenTargetId, token });
  };

  return {
    // OAuth 回跳提示
    oauthStatus,
    oauthServerName,
    oauthErrorText,
    // 列表与选中
    servers,
    selected,
    status,
    result,
    // 表单
    showForm,
    setShowForm,
    form,
    updateForm,
    formError,
    httpUrlError,
    commandError,
    canCreate,
    // 操作
    createMutation,
    testMutation,
    toggleMutation,
    autoApproveMutation,
    deleteMutation,
    oauthStartMutation,
    oauthDisconnectMutation,
    setSelectedId,
    // 详情派生
    actionError,
    showOAuth,
    oauthAuthorized,
    oauthNeedsAuthorize,
    oauthScopes,
    // oauth/start 失败分类 + 替代路径(改用令牌)
    oauthStartError,
    oauthStartRetrying: oauthStartMutation.isPending,
    retryOAuth,
    tokenDialogOpen,
    tokenSaving: tokenMutation.isPending,
    tokenError,
    openTokenDialog,
    closeTokenDialog,
    saveToken,
    refresh,
  };
}

export type McpPageModel = ReturnType<typeof useMcpPageModel>;
