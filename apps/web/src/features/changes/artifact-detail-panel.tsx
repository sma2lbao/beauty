/**
 * 产物详情(右栏):标题/类型/增删统计、单栏/并排切换、diff 或正文预览,
 * 以及底部状态行与复制/下载按钮。
 */
import { Check, Copy, Download } from 'lucide-react';

import { ArtifactPreview } from '@/features/artifacts/artifact-preview';
import { DiffViewer } from '@/components/console/diff-view';
import {
  ConsoleButton,
  ConsolePanel,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import { relativeTime } from '@/lib/presentation';
import { cn } from '@/lib/utils';

import type { ChangesModel } from './use-changes-model';

export function ArtifactDetailPanel({ model }: { model: ChangesModel }) {
  const {
    selected,
    content,
    contentPending,
    imageQuery,
    imageUrl,
    isImage,
    isText,
    isDiff,
    stats,
    view,
    setView,
    copied,
    copy,
    download,
    downloadPending,
  } = model;

  return (
    <ConsolePanel className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-ha-border px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate font-mono text-[12px] font-medium text-ha-text">
            {selected?.title ?? '选择一个产物'}
          </span>
          {selected ? (
            <ToneBadge tone="success">{selected.kind}</ToneBadge>
          ) : null}
          {isDiff && stats ? (
            <span className="hidden items-center gap-2 font-mono text-[11px] font-bold sm:flex">
              <span className="text-ha-success">+{stats.added}</span>
              <span className="text-ha-danger">-{stats.removed}</span>
            </span>
          ) : null}
        </div>

        {content && isDiff ? (
          <div className="flex shrink-0 items-center rounded-md border border-ha-border-strong bg-ha-panel p-0.5">
            {(
              [
                { key: 'inline', label: '单栏' },
                { key: 'split', label: '并排' },
              ] as const
            ).map((item) => (
              <button
                key={item.key}
                type="button"
                aria-pressed={view === item.key}
                onClick={() => setView(item.key)}
                className={cn(
                  'inline-flex h-[22px] items-center rounded-[4px] px-2.5 text-[11px] transition-colors',
                  view === item.key
                    ? 'bg-[#1c1d1f] font-medium text-ha-text'
                    : 'text-ha-muted hover:text-ha-text-2',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isImage ? (
          imageQuery.isPending ? (
            <p className="p-6 text-[12px] text-ha-dim">读取图片…</p>
          ) : imageUrl && selected ? (
            <div className="p-4">
              <ArtifactPreview artifact={selected} src={imageUrl} />
            </div>
          ) : (
            <p className="p-6 text-[12px] text-ha-danger">
              {imageQuery.error?.message ?? '图片读取失败。'}
            </p>
          )
        ) : contentPending ? (
          <p className="p-6 text-[12px] text-ha-dim">读取产物正文…</p>
        ) : content ? (
          isDiff ? (
            <DiffViewer
              content={content}
              view={view}
              numbers="double"
              className="py-2"
            />
          ) : selected ? (
            <div className="p-4">
              <ArtifactPreview
                artifact={{
                  title: selected.title,
                  kind: selected.kind,
                  content,
                  filename: selected.filename,
                  content_type: selected.content_type,
                }}
              />
            </div>
          ) : null
        ) : selected ? (
          <p className="p-6 text-[12px] text-ha-dim">
            {isText ? '产物正文为空。' : '该文件不支持在线预览，请下载后查看。'}
          </p>
        ) : (
          <p className="p-6 text-[12px] text-ha-dim">
            从左侧选择一个产物查看内容。
          </p>
        )}
      </div>

      <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-t border-ha-border px-4">
        <span className="flex min-w-0 items-center gap-2 font-mono text-[11px] text-ha-dim">
          <StatusDot tone={selected ? 'success' : 'neutral'} />
          <span className="truncate">
            {selected
              ? selected.run_id
                ? '来自运行 #' + selected.run_id.slice(0, 8)
                : '工作区产物'
              : '未选择产物'}
            {selected ? ' · ' + relativeTime(selected.created_at) : ''}
            {content ? ' · ' + content.length + ' 字符' : ''}
          </span>
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <ConsoleButton
            variant="secondary"
            size="sm"
            onClick={copy}
            disabled={!content}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-ha-success" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? '已复制' : '复制正文'}
          </ConsoleButton>
          <ConsoleButton
            variant="secondary"
            size="sm"
            onClick={download}
            disabled={!selected || downloadPending}
          >
            <Download className="h-3.5 w-3.5" />
            下载
          </ConsoleButton>
        </div>
      </div>
    </ConsolePanel>
  );
}
