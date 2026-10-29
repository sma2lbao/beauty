/**
 * 删除技能包确认弹窗:二次确认后才调用删除,失败就地提示。
 */
import { Dialog } from '@beauty/design';

import { ConsoleButton } from '@/components/console/ui';

import type { SkillsModel } from './use-skills-model';

export function SkillDeleteDialog({ model }: { model: SkillsModel }) {
  const { pendingDelete, closeDeleteDialog, deleteMutation } = model;

  if (!pendingDelete) return null;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) closeDeleteDialog();
      }}
      title="删除技能包"
      description={`将删除「${pendingDelete.title || pendingDelete.slug}」及其 ${
        pendingDelete.versions.length
      } 个版本,操作不可恢复。`}
      footer={
        <>
          <ConsoleButton
            variant="secondary"
            size="md"
            onClick={closeDeleteDialog}
          >
            取消
          </ConsoleButton>
          <ConsoleButton
            variant="danger"
            size="md"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate(pendingDelete.id)}
          >
            {deleteMutation.isPending ? '删除中…' : '确认删除'}
          </ConsoleButton>
        </>
      }
    >
      {deleteMutation.isError ? (
        <p className="text-[12px] text-ha-danger">
          删除失败:{deleteMutation.error.message}
        </p>
      ) : null}
    </Dialog>
  );
}
