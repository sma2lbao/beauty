import { Alert, Button, Spin } from '@beauty/design';
import { ShieldAlert } from 'lucide-react';

import { EmptyPanel } from '@/components/console/ui';

import type { AdminAccess, AdminModel } from './use-admin-model';

/**
 * 整页闸门:未确认权限前不渲染任何管理数据。
 * 401 → 登录失效(与 outbox 页文案一致);非 admin → 无权限;
 * 其他错误 → 可重试的失败提示,都不留白屏。
 *
 * 账号/审计/成本三个页签共用一个口径:成本页签的服务端 403 也走这里。
 */
export function AdminAccessNotice({
  access,
  onRetry,
}: {
  access: AdminAccess | null;
  onRetry: () => void;
}) {
  if (access === 'ok' || access === null) {
    return null;
  }
  if (access === 'loading') {
    return (
      <div className="mt-6">
        <Spin label="正在确认管理员权限…" />
      </div>
    );
  }
  if (access === 'unauthorized') {
    return (
      <Alert variant="danger" title="登录失效" className="mt-6">
        请重新登录后再访问管理台。
      </Alert>
    );
  }
  if (access === 'forbidden') {
    return (
      <EmptyPanel
        className="mt-6"
        icon={<ShieldAlert size={20} />}
        title="无管理员权限"
        description="管理台仅对平台管理员开放。如需跨账号治理权限,请联系平台管理员。"
      />
    );
  }
  return (
    <Alert variant="danger" className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>读取权限失败:管理员角色暂时无法确认。</span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </div>
    </Alert>
  );
}

export function AdminAccessPanel({ model }: { model: AdminModel }) {
  return (
    <AdminAccessNotice access={model.access} onRetry={model.refetchAccess} />
  );
}
