/**
 * 语义检索区:检索输入 + 检索/清空按钮 + 命中结果。
 */
import { Button, Field, Input, Spin } from '@beauty/design';

import { MemoryRows } from './memory-list';
import type { MemoryModel } from './use-memory-model';

export function MemorySearchSection({ model }: { model: MemoryModel }) {
  const {
    searchInput,
    setSearchInput,
    submittedQuery,
    submitSearch,
    clearSearch,
    searchLoading,
    hits,
  } = model;

  return (
    <>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="检索" htmlFor="memory-search" className="min-w-56 flex-1">
          <Input
            id="memory-search"
            placeholder="按语义检索记忆,例如「回复语言偏好」"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submitSearch();
            }}
          />
        </Field>
        <Button
          variant="outline"
          size="sm"
          onClick={submitSearch}
          disabled={!searchInput.trim()}
        >
          检索
        </Button>
        {submittedQuery ? (
          <Button variant="outline" size="sm" onClick={clearSearch}>
            清空检索
          </Button>
        ) : null}
      </div>

      {submittedQuery ? (
        <div className="mt-4 rounded-md border border-dashed p-3">
          {searchLoading ? (
            <Spin label="检索中…" />
          ) : hits.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              「{submittedQuery}」没有命中记忆。
            </p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                「{submittedQuery}」命中 {hits.length} 条
              </p>
              <MemoryRows model={model} memories={hits} />
            </>
          )}
        </div>
      ) : null}
    </>
  );
}
