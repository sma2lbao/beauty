/**
 * 设置页数据模型:当前账号资料与多端会话的查询/变更与表单状态。
 *
 * 页面只负责布局;各卡片组件从本 hook 取窄 props,避免把 query client 细节
 * 渗进展示层。表单值 = 本地编辑值 ?? 服务端值,不在 queryFn 里 setState。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { accountApi } from '@/lib/account-api';
import { isUnauthorized } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

export function useSettingsModel() {
  const queryClient = useQueryClient();
  // 表单值 = 本地编辑值 ?? 服务端值:不在 queryFn 里 setState(避免派生状态同步)
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [device, setDevice] = useState('');

  const accountQuery = useQuery({
    queryKey: queryKeys.accountMe,
    queryFn: accountApi.me,
  });
  const sessionsQuery = useQuery({
    queryKey: queryKeys.accountSessions,
    queryFn: accountApi.sessions,
  });

  const refreshAccount = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.accountMe });
  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.accountSessions });

  const updateMeMutation = useMutation({
    mutationFn: (nextDisplayName: string) => {
      const value = nextDisplayName.trim();
      return accountApi.updateMe({ display_name: value || null });
    },
    onSuccess: () => {
      setDisplayName(null); // 回到服务端值
      void refreshAccount();
    },
  });

  const addSessionMutation = useMutation({
    mutationFn: () => accountApi.addSession({ device: device.trim() }),
    onSuccess: async () => {
      setDevice('');
      await refreshSessions();
    },
  });

  const revokeSessionMutation = useMutation({
    mutationFn: (sessionId: string) => accountApi.revokeSession(sessionId),
    onSuccess: refreshSessions,
  });

  const account = accountQuery.data;
  const displayNameValue = displayName ?? account?.display_name ?? '';
  const sessions = sessionsQuery.data ?? [];
  const errors = [
    accountQuery.error,
    sessionsQuery.error,
    updateMeMutation.error,
    addSessionMutation.error,
    revokeSessionMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canAddSession = device.trim().length > 0;

  return {
    account,
    accountLoading: accountQuery.isLoading,
    displayNameValue,
    displayName,
    setDisplayName,
    device,
    setDevice,
    sessions,
    unauthorized,
    canAddSession,
    updateMeMutation,
    addSessionMutation,
    revokeSessionMutation,
    refreshAccount,
  };
}

export type SettingsModel = ReturnType<typeof useSettingsModel>;
