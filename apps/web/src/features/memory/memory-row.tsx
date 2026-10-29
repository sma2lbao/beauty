/**
 * 单条记忆行:徽章 + 内容(截断)+ 元信息 + 删除(二次确认)。
 */
import { Button, Tag } from '@beauty/design';

import type { Memory } from '@/lib/memory-api';
import { formatDateTime } from '@/lib/presentation';

import { KIND_COLORS, KIND_LABELS, SCOPE_LABELS } from './display';

type MemoryRowProps = {
  memory: Memory;
  workspaceName?: string;
  confirming: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
};

export function MemoryRow({
  memory,
  workspaceName,
  confirming,
  deleting,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: MemoryRowProps) {
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Tag color={KIND_COLORS[memory.kind]}>
              {KIND_LABELS[memory.kind]}
            </Tag>
            <Tag color={memory.scope === 'workspace' ? 'info' : 'neutral'}>
              {SCOPE_LABELS[memory.scope]}
            </Tag>
            {memory.pinned ? <Tag color="warning">置顶</Tag> : null}
            {memory.expires_at ? (
              <span className="text-xs text-muted-foreground">
                过期于 {formatDateTime(memory.expires_at)}
              </span>
            ) : null}
          </div>
          <p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-sm">
            {memory.content}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            来源 {memory.source_type} · 命中 {memory.access_count} 次 · 更新于{' '}
            {formatDateTime(memory.updated_at)}
            {workspaceName ? ' · 工作区 ' + workspaceName : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {confirming ? (
            <>
              <Button
                variant="danger"
                size="sm"
                disabled={deleting}
                onClick={onConfirmDelete}
              >
                {deleting ? '删除中…' : '确认删除'}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={deleting}
                onClick={onCancelDelete}
              >
                取消
              </Button>
            </>
          ) : (
            <Button variant="outline" size="sm" onClick={onRequestDelete}>
              删除
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}
