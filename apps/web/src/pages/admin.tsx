/**
 * 管理台(P0-7):平台运营的跨账号治理入口,只对 role === 'admin' 开放。
 * 页面只做组装:权限闸门、页签与数据都在 features/admin 里。
 */
import { ConsoleTabs, PageHeader } from '@/components/console/ui';
import { AdminAccessPanel } from '@/features/admin/admin-access-panel';
import { AdminAccountsPanel } from '@/features/admin/admin-accounts-panel';
import { AdminAuditPanel } from '@/features/admin/admin-audit-panel';
import {
  useAdminModel,
  type AdminTab,
} from '@/features/admin/use-admin-model';

const TABS: { value: AdminTab; label: string }[] = [
  { value: 'accounts', label: '账号' },
  { value: 'audit', label: '审计日志' },
];

export function AdminPage() {
  const model = useAdminModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="管理台"
        subtitle="平台运营 · 跨账号治理,所有写操作留痕"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AdminAccessPanel model={model} />

        {model.access === 'ok' ? (
          <>
            <ConsoleTabs
              className="mt-4"
              items={TABS}
              value={model.tab}
              onChange={model.setTab}
            />
            {model.tab === 'accounts' ? (
              <AdminAccountsPanel model={model} />
            ) : (
              <AdminAuditPanel model={model} />
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
