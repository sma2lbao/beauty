/**
 * 记忆列表:加载态 / 空态 / 行列表(主列表与检索命中共用行渲染)。
 */
import { Empty, Spin } from '@beauty/design';

import type { Memory } from '@/lib/memory-api';

import { MemoryRow } from './memory-row';
import type { MemoryModel } from './use-memory-model';

/** 行列表:删除的二次确认状态由 model 统一管理。 */
export function MemoryRows({
  model,
  memories,
}: {
  model: MemoryModel;
  memories: Memory[];
}) {
  const { workspaceNames, confirmingId, setConfirmingId, removeMutation } =
    model;

  return (
    <ul className="divide-y">
      {memories.map((memory) => (
        <MemoryRow
          key={memory.id}
          memory={memory}
          workspaceName={
            memory.workspace_id
              ? workspaceNames.get(memory.workspace_id)
              : undefined
          }
          confirming={confirmingId === memory.id}
          deleting={
            removeMutation.isPending && removeMutation.variables === memory.id
          }
          onRequestDelete={() => setConfirmingId(memory.id)}
          onConfirmDelete={() => removeMutation.mutate(memory.id)}
          onCancelDelete={() => setConfirmingId(null)}
        />
      ))}
    </ul>
  );
}

export function MemoryList({ model }: { model: MemoryModel }) {
  const { memories, listLoading } = model;

  if (listLoading) {
    return <Spin label="加载中…" />;
  }
  if (memories.length === 0) {
    return (
      <Empty description="还没有记忆:在右侧写入偏好或事实,下次对话会自动带上。" />
    );
  }
  return <MemoryRows model={model} memories={memories} />;
}
