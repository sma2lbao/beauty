/**
 * 单条记忆行:徽章 + 内容(可切换行内编辑)+ 元信息 + 置顶/编辑/删除(二次确认)。
 * 已失效条目展示替代链提示,便于回看旧事实。
 */
import { Button, Tag, Textarea } from '@beauty/design';

import type { Memory } from '@/lib/memory-api';
import { formatDateTime } from '@/lib/presentation';

import {
  INJECTION_LABELS,
  KIND_COLORS,
  KIND_LABELS,
  SCOPE_LABELS,
  SOURCE_LABELS,
} from './display';

type MemoryRowProps = {
  memory: Memory;
  workspaceName?: string;
  confirming: boolean;
  deleting: boolean;
  editing: boolean;
  editingContent: string;
  saving: boolean;
  onEditingContentChange: (value: string) => void;
  onStartEdit: () => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onTogglePinned: () => void;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
};

export function MemoryRow({
  memory,
  workspaceName,
  confirming,
  deleting,
  editing,
  editingContent,
  saving,
  onEditingContentChange,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onTogglePinned,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: MemoryRowProps) {
  const invalid = Boolean(memory.invalid_at);
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
            {memory.injection_mode === 'always' ? (
              <Tag color="primary">{INJECTION_LABELS.always}</Tag>
            ) : null}
            {invalid ? <Tag color="danger">已失效</Tag> : null}
            {memory.expires_at ? (
              <span className="text-xs text-muted-foreground">
                过期于 {formatDateTime(memory.expires_at)}
              </span>
            ) : null}
          </div>

          {editing ? (
            <div className="mt-2 space-y-2">
              <Textarea
                aria-label="编辑记忆内容"
                rows={3}
                value={editingContent}
                onChange={(event) => onEditingContentChange(event.target.value)}
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={saving || editingContent.trim().length === 0}
                  onClick={onSaveEdit}
                >
                  {saving ? '保存中…' : '保存'}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={saving}
                  onClick={onCancelEdit}
                >
                  取消编辑
                </Button>
              </div>
            </div>
          ) : (
            <p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-sm">
              {memory.content}
            </p>
          )}

          <p className="mt-1 text-xs text-muted-foreground">
            来源 {SOURCE_LABELS[memory.source_type] ?? memory.source_type} ·
            命中 {memory.access_count} 次 · 更新于{' '}
            {formatDateTime(memory.updated_at)}
            {workspaceName ? ' · 工作区 ' + workspaceName : ''}
            {memory.confidence < 1
              ? ' · 置信度 ' + memory.confidence.toFixed(2)
              : ''}
            {invalid
              ? ' · 失效于 ' + formatDateTime(memory.invalid_at as string)
              : ''}
            {memory.superseded_by ? ' · 已被新记忆替代' : ''}
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
            <>
              <Button
                variant="outline"
                size="sm"
                disabled={saving || invalid}
                onClick={onTogglePinned}
              >
                {memory.pinned ? '取消置顶' : '置顶'}
              </Button>
              {editing ? null : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={invalid}
                  onClick={onStartEdit}
                >
                  编辑
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={onRequestDelete}>
                删除
              </Button>
            </>
          )}
        </div>
      </div>
    </li>
  );
}
