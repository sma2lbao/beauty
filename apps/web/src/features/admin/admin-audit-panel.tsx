import { Alert, Button, Input, Spin, Tag } from '@beauty/design';
import { Fragment, useState } from 'react';

import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import type { AuditActionStatus } from '@/lib/admin-api';
import { formatDateTime } from '@/lib/presentation';

import { AdminPagination } from './admin-pagination';
import type { AdminModel } from './use-admin-model';

const STATUS_META: Record<
  AuditActionStatus,
  { label: string; color: 'success' | 'danger' }
> = {
  success: { label: '成功', color: 'success' },
  denied: { label: '拒绝', color: 'danger' },
  failed: { label: '失败', color: 'danger' },
};

/**
 * 审计页签:只读的追加日志。过滤条件为精确匹配(action / actor / target),
 * detail 用展开行看原始 JSON —— 不解析成列,避免契约演进时前端跟着漂。
 */
export function AdminAuditPanel({ model }: { model: AdminModel }) {
  const { audit } = model;
  const [draftAction, setDraftAction] = useState(audit.action);
  const [draftActor, setDraftActor] = useState(audit.actorAccountId);
  const [draftTarget, setDraftTarget] = useState(audit.targetId);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const rows = audit.data?.items ?? [];
  const total = audit.data?.total ?? 0;

  return (
    <div>
      <form
        className="mt-4 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          audit.applyFilters({
            action: draftAction,
            actorAccountId: draftActor,
            targetId: draftTarget,
          });
        }}
      >
        <div className="min-w-[180px] flex-1">
          <label
            htmlFor="admin-audit-action"
            className="mb-1 block text-[11px] text-ha-dim"
          >
            action
          </label>
          <Input
            id="admin-audit-action"
            value={draftAction}
            placeholder="如 account.role.update"
            onChange={(event) => setDraftAction(event.target.value)}
          />
        </div>
        <div className="min-w-[180px] flex-1">
          <label
            htmlFor="admin-audit-actor"
            className="mb-1 block text-[11px] text-ha-dim"
          >
            actor_account_id
          </label>
          <Input
            id="admin-audit-actor"
            value={draftActor}
            placeholder="操作者账号 ID"
            onChange={(event) => setDraftActor(event.target.value)}
          />
        </div>
        <div className="min-w-[180px] flex-1">
          <label
            htmlFor="admin-audit-target"
            className="mb-1 block text-[11px] text-ha-dim"
          >
            target_id
          </label>
          <Input
            id="admin-audit-target"
            value={draftTarget}
            placeholder="目标 ID"
            onChange={(event) => setDraftTarget(event.target.value)}
          />
        </div>
        <Button type="submit" variant="outline" size="sm">
          查询
        </Button>
      </form>

      {audit.error ? (
        <Alert variant="danger" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>读取审计日志失败:{(audit.error as Error).message}</span>
            <Button variant="outline" size="sm" onClick={audit.refetch}>
              重试
            </Button>
          </div>
        </Alert>
      ) : audit.isLoading ? (
        <div className="mt-6">
          <Spin label="加载审计日志…" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyPanel
          className="mt-6"
          title="没有审计记录"
          description="管理操作与越权访问都会记录在这里;清空过滤条件可看全部。"
        />
      ) : (
        <ConsolePanel className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[1040px] text-left text-[12px]">
            <thead className="border-b border-ha-border text-[11px] text-ha-dim">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  时间
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  操作者
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  action
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  目标
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  状态
                </th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  request_id
                </th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">
                  详情
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ha-border">
              {rows.map((log) => {
                const expanded = expandedId === log.id;
                const meta = STATUS_META[log.status];
                return (
                  <Fragment key={log.id}>
                    <tr>
                      <td className="px-4 py-3 font-mono text-[11px] text-ha-dim">
                        {formatDateTime(log.created_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-ha-text-2">{log.actor_email}</div>
                        <div className="mt-0.5 font-mono text-[11px] text-ha-dim">
                          {log.actor_account_id}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-ha-text-2">{log.action}</td>
                      <td className="px-4 py-3 font-mono text-[11px] text-ha-dim">
                        {log.target_type + ':' + log.target_id}
                      </td>
                      <td className="px-4 py-3">
                        <Tag color={meta.color}>{meta.label}</Tag>
                      </td>
                      <td className="px-4 py-3 font-mono text-[11px] text-ha-dim">
                        {log.request_id ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-expanded={expanded}
                          onClick={() => setExpandedId(expanded ? null : log.id)}
                        >
                          {expanded ? '收起' : '详情'}
                        </Button>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr>
                        <td colSpan={7} className="bg-ha-panel-2/40 px-4 py-3">
                          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-ha-text-2">
                            {JSON.stringify(log.detail, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <AdminPagination
            offset={audit.offset}
            total={total}
            onChange={audit.setOffset}
          />
        </ConsolePanel>
      )}
    </div>
  );
}
