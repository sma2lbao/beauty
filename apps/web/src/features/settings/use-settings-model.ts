/**
 * 设置页数据模型:组织档案、成员、多端会话的查询/变更与表单状态。
 *
 * 页面只负责布局;各卡片组件从本 hook 取窄 props,避免把 query client 细节
 * 渗进展示层。表单值 = 本地编辑值 ?? 服务端值,不在 queryFn 里 setState。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { Role } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import { isUnauthorized } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

export function useSettingsModel() {
  const queryClient = useQueryClient();
  // 表单值 = 本地编辑值 ?? 服务端值:不在 queryFn 里 setState(避免派生状态同步)
  const [orgName, setOrgName] = useState<string | null>(null);
  const [orgPlan, setOrgPlan] = useState<string | null>(null);
  const [quotaText, setQuotaText] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [subject, setSubject] = useState('');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<Role>('member');
  const [device, setDevice] = useState('');
  const [sessionUserId, setSessionUserId] = useState('');

  const orgQuery = useQuery({
    queryKey: queryKeys.accountOrganization,
    queryFn: accountApi.organization,
  });
  const membersQuery = useQuery({
    queryKey: queryKeys.accountMembers,
    queryFn: accountApi.members,
  });
  const sessionsQuery = useQuery({
    queryKey: queryKeys.accountSessions,
    queryFn: accountApi.sessions,
  });

  const refreshOrg = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.accountOrganization });
  const refreshMembers = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.accountMembers });
  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.accountSessions });

  const orgMutation = useMutation({
    mutationFn: async (input: {
      name: string;
      plan: string;
      quota: string;
    }) => {
      let quota: Record<string, unknown> | undefined;
      if (input.quota.trim()) {
        try {
          quota = JSON.parse(input.quota) as Record<string, unknown>;
        } catch {
          throw new Error('配额必须是合法 JSON,例如 {"seats": 20}');
        }
      }
      return accountApi.updateOrganization({
        name: input.name.trim() || undefined,
        plan: input.plan.trim() || undefined,
        quota,
      });
    },
    onSuccess: () => {
      setQuotaError(null);
      setQuotaText(null); // 回到服务端值
      void refreshOrg();
    },
    onError: (error: Error) => setQuotaError(error.message),
  });

  const addMemberMutation = useMutation({
    mutationFn: () =>
      accountApi.addMember({
        subject: subject.trim(),
        email: email.trim(),
        display_name: displayName.trim() || null,
        role,
      }),
    onSuccess: async () => {
      setSubject('');
      setEmail('');
      setDisplayName('');
      await refreshMembers();
    },
  });

  const updateMemberMutation = useMutation({
    mutationFn: (input: {
      memberId: string;
      patch: { role?: Role; status?: 'active' | 'disabled' };
    }) => accountApi.updateMember(input.memberId, input.patch),
    onSuccess: refreshMembers,
  });

  const addSessionMutation = useMutation({
    mutationFn: () =>
      accountApi.addSession({
        user_id: sessionUserId.trim(),
        device: device.trim(),
      }),
    onSuccess: async () => {
      setDevice('');
      await refreshSessions();
    },
  });

  const revokeSessionMutation = useMutation({
    mutationFn: (sessionId: string) => accountApi.revokeSession(sessionId),
    onSuccess: refreshSessions,
  });

  const organization = orgQuery.data;
  const nameValue = orgName ?? organization?.name ?? '';
  const planValue = orgPlan ?? organization?.plan ?? '';
  const quotaValue = quotaText ?? JSON.stringify(organization?.quota ?? {});
  const members = membersQuery.data ?? [];
  const sessions = sessionsQuery.data ?? [];
  const errors = [
    orgQuery.error,
    membersQuery.error,
    sessionsQuery.error,
    orgMutation.error,
    addMemberMutation.error,
    updateMemberMutation.error,
    addSessionMutation.error,
    revokeSessionMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canAddMember = subject.trim().length > 0 && email.trim().length > 0;
  const canAddSession =
    sessionUserId.trim().length > 0 && device.trim().length > 0;

  return {
    organization,
    nameValue,
    planValue,
    quotaValue,
    members,
    sessions,
    unauthorized,
    canAddMember,
    canAddSession,
    orgName,
    orgPlan,
    quotaText,
    setOrgName,
    setOrgPlan,
    setQuotaText,
    quotaError,
    subject,
    setSubject,
    email,
    setEmail,
    displayName,
    setDisplayName,
    role,
    setRole,
    device,
    setDevice,
    sessionUserId,
    setSessionUserId,
    orgMutation,
    addMemberMutation,
    updateMemberMutation,
    addSessionMutation,
    revokeSessionMutation,
    refreshOrg,
    membersLoading: membersQuery.isLoading,
  };
}

export type SettingsModel = ReturnType<typeof useSettingsModel>;
