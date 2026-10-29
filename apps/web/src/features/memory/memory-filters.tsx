/**
 * 记忆列表头部的类型 / 工作区过滤下拉。
 */
import { Select } from '@beauty/design';

import { MEMORY_KINDS, type MemoryKind } from '@/lib/memory-api';

import { KIND_LABELS } from './display';
import type { MemoryModel } from './use-memory-model';

export function MemoryFilters({ model }: { model: MemoryModel }) {
  const {
    kindFilter,
    setKindFilter,
    workspaceFilter,
    setWorkspaceFilter,
    workspaces,
  } = model;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label
        className="text-sm text-muted-foreground"
        htmlFor="memory-kind-filter"
      >
        类型
      </label>
      <Select
        id="memory-kind-filter"
        className="w-32"
        value={kindFilter}
        onChange={(event) =>
          setKindFilter(event.target.value as MemoryKind | '')
        }
      >
        <option value="">全部</option>
        {MEMORY_KINDS.map((kind) => (
          <option key={kind} value={kind}>
            {KIND_LABELS[kind]}
          </option>
        ))}
      </Select>
      <label
        className="text-sm text-muted-foreground"
        htmlFor="memory-workspace-filter"
      >
        工作区
      </label>
      <Select
        id="memory-workspace-filter"
        className="w-40"
        value={workspaceFilter}
        onChange={(event) => setWorkspaceFilter(event.target.value)}
      >
        <option value="">全部</option>
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </Select>
    </div>
  );
}
