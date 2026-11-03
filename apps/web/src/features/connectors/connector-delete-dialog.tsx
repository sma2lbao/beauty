/**
 * 解除连接确认弹窗:撤销后凭证立即删除,并提示会话里的工具会随之失效。
 */
import { ConsoleButton } from '@/components/console/ui';
import { ConsoleDialog } from '@/components/console/dialog';
import type { ConnectorConnection } from '@/lib/connector-api';

import { connectionName } from './display';

export type ConnectorDeleteDialogProps = {
  connection: ConnectorConnection | null;
  providerName: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConnectorDeleteDialog({
  connection,
  providerName,
  pending,
  onCancel,
  onConfirm,
}: ConnectorDeleteDialogProps) {
  return (
    <ConsoleDialog
      open={connection !== null}
      title="解除连接？"
      description={
        connection
          ? `将删除 ${connectionName(connection)}（${providerName}）的授权凭证。`
          : undefined
      }
      onClose={onCancel}
      footer={
        <>
          <ConsoleButton variant="ghost" size="sm" onClick={onCancel}>
            取消
          </ConsoleButton>
          <ConsoleButton
            variant="danger"
            size="sm"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? '解除中…' : '确认解除'}
          </ConsoleButton>
        </>
      }
    >
      <p className="text-xs leading-5 text-ha-text-2">
        解除后该连接立刻不可用，会话里依赖它的工具会失效；如需恢复必须重新走一遍授权。
      </p>
    </ConsoleDialog>
  );
}
