/**
 * 管理台的数据与交互模型(P0-7):账号列表(搜索/状态/分页)、审计日志
 * (过滤/分页)与配额/角色/状态三类写操作。页面与面板只消费这里的状态。
 *
 * 访问闸门:me 给出角色;401 → 登录失效、非 admin → 无权限。
 * 账号/审计查询若返回 401/403(角色被撤销),同样提升成整页闸门;
 * 其他查询错误留在面板内显示 + 重试,不白屏。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { useAccountRole } from '@/features/auth/use-account-role';
import {
  adminApi,
  type AdminRole,
  type AdminStatus,
  type QuotaPatch,
} from '@/lib/admin-api';
import { ApiError, isUnauthorized } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

export const ADMIN_PAGE_SIZE = 20;

/** 管理台页签:账号治理 / 审计日志 / 成本看板(P1-K)。 */
export type AdminTab = 'accounts' | 'audit' | 'cost';

/** 整页闸门:加载中 / 可访问 / 未登录 / 非管理员 / 读取失败。 */
export type AdminAccess =
  | 'loading'
  | 'ok'
  | 'unauthorized'
  | 'forbidden'
  | 'error';

/** 查询错误里只有鉴权类错误值得升级成整页闸门。 */
function accessFromError(error: unknown): 'unauthorized' | 'forbidden' | null {
  if (isUnauthorized(error)) {
    return 'unauthorized';
  }
  if (error instanceof ApiError && error.status === 403) {
    return 'forbidden';
  }
  return null;
}

export function useAdminModel() {
  const queryClient = useQueryClient();
  const me = useAccountRole();
  const [tab, setTab] = useState<AdminTab>('accounts');

  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<AdminStatus | ''>('');
  const [accountOffset, setAccountOffset] = useState(0);

  const [auditAction, setAuditAction] = useState('');
  const [auditActor, setAuditActor] = useState('');
  const [auditTarget, setAuditTarget] = useState('');
  const [auditOffset, setAuditOffset] = useState(0);

  const accountsQuery = useQuery({
    queryKey: queryKeys.adminAccountList(
      query,
      status,
      ADMIN_PAGE_SIZE,
      accountOffset,
    ),
    queryFn: () =>
      adminApi.listAccounts({
        q: query || undefined,
        status: status || undefined,
        limit: ADMIN_PAGE_SIZE,
        offset: accountOffset,
      }),
    enabled: me.isAdmin,
  });

  const auditQuery = useQuery({
    queryKey: queryKeys.adminAuditLogList(
      ADMIN_PAGE_SIZE,
      auditOffset,
      auditActor,
      auditAction,
      auditTarget,
    ),
    queryFn: () =>
      adminApi.listAuditLogs({
        limit: ADMIN_PAGE_SIZE,
        offset: auditOffset,
        actor_account_id: auditActor || undefined,
        action: auditAction || undefined,
        target_id: auditTarget || undefined,
      }),
    enabled: me.isAdmin && tab === 'audit',
  });

  // 写操作成功:账号行与审计都要刷新(写操作本身会新增一条审计)。
  const refreshAfterWrite = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.adminAccounts }),
      queryClient.invalidateQueries({ queryKey: queryKeys.adminAuditLogs }),
    ]);
  };

  const quotaMutation = useMutation({
    mutationFn: (input: { accountId: string; patch: QuotaPatch }) =>
      adminApi.updateQuota(input.accountId, input.patch),
    onSuccess: refreshAfterWrite,
  });
  const roleMutation = useMutation({
    mutationFn: (input: { accountId: string; role: AdminRole }) =>
      adminApi.updateRole(input.accountId, { role: input.role }),
    onSuccess: refreshAfterWrite,
  });
  const statusMutation = useMutation({
    mutationFn: (input: { accountId: string; status: AdminStatus }) =>
      adminApi.updateStatus(input.accountId, { status: input.status }),
    onSuccess: refreshAfterWrite,
  });

  const accounts = {
    query,
    status,
    offset: accountOffset,
    data: accountsQuery.data,
    isLoading: accountsQuery.isPending,
    error: accountsQuery.error,
    refetch: () => void accountsQuery.refetch(),
    applyFilters: (next: { query: string; status: AdminStatus | '' }) => {
      setQuery(next.query.trim());
      setStatus(next.status);
      setAccountOffset(0);
    },
    setOffset: setAccountOffset,
  };

  const audit = {
    action: auditAction,
    actorAccountId: auditActor,
    targetId: auditTarget,
    offset: auditOffset,
    data: auditQuery.data,
    isLoading: auditQuery.isPending,
    error: auditQuery.error,
    refetch: () => void auditQuery.refetch(),
    applyFilters: (next: {
      action: string;
      actorAccountId: string;
      targetId: string;
    }) => {
      setAuditAction(next.action.trim());
      setAuditActor(next.actorAccountId.trim());
      setAuditTarget(next.targetId.trim());
      setAuditOffset(0);
    },
    setOffset: setAuditOffset,
  };

  const roleAccess: AdminAccess = me.error
    ? (accessFromError(me.error) ?? 'error')
    : me.isLoading
      ? 'loading'
      : me.isAdmin
        ? 'ok'
        : 'forbidden';
  const queryAccess =
    accessFromError(accountsQuery.error) ??
    accessFromError(auditQuery.error) ??
    null;
  const access: AdminAccess = queryAccess ?? roleAccess;

  return {
    tab,
    setTab,
    access,
    selfAccountId: me.account?.id,
    refetchAccess: () => void me.refetch(),
    accounts,
    audit,
    quotaMutation,
    roleMutation,
    statusMutation,
  };
}

export type AdminModel = ReturnType<typeof useAdminModel>;
