import { Alert, Button, Input, Select, Spin, Tag } from '@beauty/design';
import { useState } from 'react';

import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import type { AdminAccount, AdminRole, AdminStatus } from '@/lib/admin-api';
import { formatDateTime, formatUsd } from '@/lib/presentation';

import { formatNumber } from '../usage/format-number';
import { AdminConfirmDialog } from './admin-confirm-dialog';
import { AdminPagination } from './admin-pagination';
import { AdminQuotaDialog } from './admin-quota-dialog';
import type { AdminModel } from './use-admin-model';

const ROLE_LABELS: Record<AdminRole, string> = {
  user: '普通用户',
  admin: '管理员',
};

const STATUS_LABELS: Record<AdminStatus, string> = {
  active: '正常',
  disabled: '已停用',
};

/** quota 字段缺省/null 都表示不限,与后端语义一致。 */
function quotaLabel(account: AdminAccount): string {
  const parts: string[] = [];
  if (account.quota.monthly_tokens != null) {
    parts.push(formatNumber(account.quota.monthly_tokens) + ' tokens/月');
  }
  if (account.quota.max_concurrent_runs != null) {
    parts.push(account.quota.max_concurrent_runs + ' 并发');
  }
  return parts.length > 0 ? parts.join(' · ') : '不限';
}

/**
 * 账号治理页签:搜索(邮箱/显示名)、状态筛选、分页表格与行操作。
 * 自己的账号不允许改角色/停用(后端也会 409),按钮直接禁用并给出说明。
 */
export function AdminAccountsPanel({ model }: { model: AdminModel }) {
  const { accounts } = model;
  const [draftQuery, setDraftQuery] = useState(accounts.query);
  const [draftStatus, setDraftStatus] = useState<AdminStatus | ''>(accounts.status);
  const [quotaTarget, setQuotaTarget] = useState<AdminAccount | null>(null);
  const [roleTarget, setRoleTarget] = useState<{
    account: AdminAccount;
    next: AdminRole;
  } | null>(null);
  const [statusTarget, setStatusTarget] = useState<{
    account: AdminAccount;
    next: AdminStatus;
  } | null>(null);

  const rows = accounts.data?.items ?? [];
  const total = accounts.data?.total ?? 0;
  const selfId = model.selfAccountId;

  const closeQuota = () => {
    model.quotaMutation.reset();
    setQuotaTarget(null);
  };
  const closeRole = () => {
    model.roleMutation.reset();
    setRoleTarget(null);
  };
  const closeStatus = () => {
    model.statusMutation.reset();
    setStatusTarget(null);
  };

  return (
    <div>
      <form
        className="mt-4 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          accounts.applyFilters({ query: draftQuery, status: draftStatus });
        }}
      >
        <div className="min-w-[220px] flex-1">
          <label
            htmlFor="admin-account-query"
            className="mb-1 block text-[11px] text-ha-dim"
          >
            搜索账号
          </label>
          <Input
            id="admin-account-query"
            value={draftQuery}
            placeholder="邮箱或显示名(大小写不敏感)"
            onChange={(event) => setDraftQuery(event.target.value)}
          />
        </div>
        <div>
          <label
            htmlFor="admin-account-status"
            className="mb-1 block text-[11px] text-ha-dim"
          >
            状态
          </label>
          <Select
            id="admin-account-status"
            value={draftStatus}
            onChange={(event) => {
              const next = event.target.value as AdminStatus | '';
              setDraftStatus(next);
              // 状态切换即查询,offset 由 model 归零。
              accounts.applyFilters({ query: draftQuery, status: next });
            }}
          >
            <option value="">全部</option>
            <option value="active">正常</option>
            <option value="disabled">已停用</option>
          </Select>
        </div>
        <Button type="submit" variant="outline" size="sm">
          查询
        </Button>
      </form>

      {accounts.error ? (
        <Alert variant="danger" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>读取账号失败:{(accounts.error as Error).message}</span>
            <Button variant="outline" size="sm" onClick={accounts.refetch}>
              重试
            </Button>
          </div>
        </Alert>
      ) : accounts.isLoading ? (
        <div className="mt-6">
          <Spin label="加载账号…" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyPanel
          className="mt-6"
          title="没有匹配的账号"
          description="换个搜索词或状态筛选试试;账号由用户注册产生。"
        />
      ) : (
        <ConsolePanel className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[960px] text-left text-[12px]">
            <thead className="border-b border-ha-border text-[11px] text-ha-dim">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  账号
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  状态
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  角色
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  本月 tokens
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  本月成本
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  配额
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  创建时间
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ha-border">
              {rows.map((account) => {
                const isSelf = account.id === selfId;
                return (
                  <tr key={account.id}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-ha-text-2">{account.email}</div>
                      <div className="mt-0.5 text-[11px] text-ha-dim">
                        {account.display_name ?? '未设置显示名'}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Tag color={account.status === 'active' ? 'success' : 'danger'}>
                        {STATUS_LABELS[account.status]}
                      </Tag>
                    </td>
                    <td className="px-4 py-3">
                      <Tag color={account.role === 'admin' ? 'primary' : 'neutral'}>
                        {ROLE_LABELS[account.role]}
                      </Tag>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-ha-text-2">
                      {formatNumber(account.usage.month_tokens)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-ha-text-2">
                      {formatUsd(account.usage.month_cost_usd)}
                    </td>
                    <td className="px-4 py-3 text-ha-text-2">{quotaLabel(account)}</td>
                    <td className="px-4 py-3 font-mono text-[11px] text-ha-dim">
                      {formatDateTime(account.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center justify-end gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setQuotaTarget(account)}
                        >
                          编辑配额
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={isSelf}
                          title={isSelf ? '不能修改自己的角色' : undefined}
                          onClick={() =>
                            setRoleTarget({
                              account,
                              next: account.role === 'admin' ? 'user' : 'admin',
                            })
                          }
                        >
                          {account.role === 'admin' ? '降为普通用户' : '设为管理员'}
                        </Button>
                        <Button
                          variant={account.status === 'active' ? 'danger' : 'outline'}
                          size="sm"
                          disabled={isSelf}
                          title={isSelf ? '不能停用自己的账号' : undefined}
                          onClick={() =>
                            setStatusTarget({
                              account,
                              next: account.status === 'active' ? 'disabled' : 'active',
                            })
                          }
                        >
                          {account.status === 'active' ? '停用' : '启用'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <AdminPagination
            offset={accounts.offset}
            total={total}
            onChange={accounts.setOffset}
          />
        </ConsolePanel>
      )}

      {quotaTarget ? (
        <AdminQuotaDialog
          account={quotaTarget}
          pending={model.quotaMutation.isPending}
          error={model.quotaMutation.error}
          onClose={closeQuota}
          onSubmit={(patch) =>
            model.quotaMutation.mutate(
              { accountId: quotaTarget.id, patch },
              { onSuccess: closeQuota },
            )
          }
        />
      ) : null}

      <AdminConfirmDialog
        open={roleTarget !== null}
        title="修改角色"
        description={
          roleTarget
            ? roleTarget.account.email +
              (roleTarget.next === 'admin'
                ? ':设为管理员后可访问管理台并跨账号治理。'
                : ':降为普通用户后将失去管理台入口。')
            : undefined
        }
        confirmLabel={
          roleTarget?.next === 'admin' ? '确认设为管理员' : '确认降为普通用户'
        }
        pending={model.roleMutation.isPending}
        error={model.roleMutation.error}
        onClose={closeRole}
        onConfirm={() => {
          if (!roleTarget) {
            return;
          }
          model.roleMutation.mutate(
            { accountId: roleTarget.account.id, role: roleTarget.next },
            { onSuccess: closeRole },
          );
        }}
      />

      <AdminConfirmDialog
        open={statusTarget !== null}
        title={statusTarget?.next === 'disabled' ? '停用账号' : '启用账号'}
        description={
          statusTarget
            ? statusTarget.account.email +
              (statusTarget.next === 'disabled'
                ? ':停用后该账号无法登录,已有令牌也会被拒绝(可随时重新启用)。'
                : ':启用后该账号即可恢复登录。')
            : undefined
        }
        confirmLabel={statusTarget?.next === 'disabled' ? '停用账号' : '启用账号'}
        pending={model.statusMutation.isPending}
        error={model.statusMutation.error}
        onClose={closeStatus}
        onConfirm={() => {
          if (!statusTarget) {
            return;
          }
          model.statusMutation.mutate(
            { accountId: statusTarget.account.id, status: statusTarget.next },
            { onSuccess: closeStatus },
          );
        }}
      />
    </div>
  );
}
