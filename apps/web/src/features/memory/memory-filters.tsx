/**
 * 记忆列表头部的类型 / 工作区 / 来源过滤与「显示失效条目」开关。
 */
import { Checkbox, Select } from '@beauty/design';

import {
  MEMORY_KINDS,
  MEMORY_SOURCES,
  type MemoryKind,
  type MemorySource,
} from '@/lib/memory-api';

import { KIND_LABELS, SOURCE_LABELS } from './display';
import type { MemoryModel } from './use-memory-model';

export function MemoryFilters({ model }: { model: MemoryModel }) {
  const {
    kindFilter,
    setKindFilter,
    workspaceFilter,
    setWorkspaceFilter,
    sourceFilter,
    setSourceFilter,
    includeInvalid,
    setIncludeInvalid,
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
      <label
        className="text-sm text-muted-foreground"
        htmlFor="memory-source-filter"
      >
        来源
      </label>
      <Select
        id="memory-source-filter"
        className="w-32"
        value={sourceFilter}
        onChange={(event) =>
          setSourceFilter(event.target.value as MemorySource | '')
        }
      >
        <option value="">全部</option>
        {MEMORY_SOURCES.map((source) => (
          <option key={source} value={source}>
            {SOURCE_LABELS[source] ?? source}
          </option>
        ))}
      </Select>
      <Checkbox
        id="memory-include-invalid"
        checked={includeInvalid}
        onCheckedChange={(checked) => setIncludeInvalid(checked)}
        label="显示失效"
      />
    </div>
  );
}
