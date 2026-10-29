/**
 * 左列:记忆列表卡片(标题/计数 + 过滤 + 检索 + 列表 + 变更错误)。
 */
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@beauty/design';

import { MemoryFilters } from './memory-filters';
import { MemoryList } from './memory-list';
import { MemorySearchSection } from './memory-search-section';
import type { MemoryModel } from './use-memory-model';

export function MemoryListPanel({ model }: { model: MemoryModel }) {
  const { memories, listLoading, mutationError } = model;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>记忆列表</CardTitle>
            <CardDescription>
              {listLoading
                ? '加载中…'
                : '共 ' + memories.length + ' 条(最新在前)'}
            </CardDescription>
          </div>
          <MemoryFilters model={model} />
        </div>
      </CardHeader>
      <CardContent>
        <MemorySearchSection model={model} />

        <div className="mt-4">
          <MemoryList model={model} />
        </div>

        {mutationError ? (
          <p className="mt-3 text-sm text-destructive">
            {mutationError instanceof Error
              ? mutationError.message
              : '操作失败'}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
