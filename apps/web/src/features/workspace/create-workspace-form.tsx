/**
 * 工作区内联新建表单:名称 + 可选描述,提交后由模型补缓存并切换 URL。
 */
import { Field, Input } from '@beauty/design';

import { ConsoleButton } from '@/components/console/ui';

import type { WorkspacePageModel } from './use-workspace-page';

export function CreateWorkspaceForm({ model }: { model: WorkspacePageModel }) {
  const {
    name,
    setName,
    description,
    setDescription,
    canCreate,
    formOpen,
    setFormOpen,
    createMutation,
  } = model;

  return (
    <form
      className="w-full space-y-3 text-left"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canCreate || createMutation.isPending) return;
        createMutation.mutate();
      }}
    >
      <Field label="工作区名称" htmlFor="workspace-name" required>
        <Input
          id="workspace-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="例如:销售资料"
        />
      </Field>
      <Field label="工作区描述(可选)" htmlFor="workspace-description">
        <Input
          id="workspace-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="这个工作区收哪些会话和交付物"
        />
      </Field>
      <div className="flex items-center gap-2">
        <ConsoleButton
          type="submit"
          variant="primary"
          size="sm"
          disabled={!canCreate || createMutation.isPending}
        >
          {createMutation.isPending ? '创建中…' : '创建工作区'}
        </ConsoleButton>
        {formOpen ? (
          <ConsoleButton
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setFormOpen(false)}
          >
            收起
          </ConsoleButton>
        ) : null}
      </div>
      {createMutation.isError ? (
        <p className="text-[12px] text-ha-danger">
          {(createMutation.error as Error).message}
        </p>
      ) : null}
    </form>
  );
}
