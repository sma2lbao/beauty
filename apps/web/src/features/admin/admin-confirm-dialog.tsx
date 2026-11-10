import { Button } from '@beauty/design';

import { ConsoleDialog } from '@/components/console/dialog';

/**
 * 角色/状态等二元写操作的确认弹窗:把"改什么、对谁改"写清楚,
 * 失败时把服务端错误留在弹窗里(不关闭),让管理员能重试。
 */
export function AdminConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pending,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  pending: boolean;
  error: unknown;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <ConsoleDialog
      open={open}
      title={title}
      description={description}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" size="sm" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button variant="danger" size="sm" disabled={pending} onClick={onConfirm}>
            {pending ? '提交中…' : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-[12px] text-ha-muted">
          所有管理操作都会记入审计日志(action、操作者、目标、时间),不可删除。
        </p>
        {error ? (
          <div role="alert" className="text-[12px] text-ha-danger">
            操作失败:{(error as Error).message}
          </div>
        ) : null}
      </div>
    </ConsoleDialog>
  );
}
