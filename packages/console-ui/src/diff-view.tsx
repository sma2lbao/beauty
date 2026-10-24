/**
 * Diff / 代码查看器:工作台的工具结果卡与「变更与产物」页共用。
 *
 * 后端没有专门的 diff 字段:工具结果或产物正文里如果带 unified diff 标记
 * (---/+++/@@),就按增删行渲染;否则老老实实回退成带行号的代码预览——
 * 不为了好看伪造 diff。
 *
 * 从 apps/web/src/components/console/diff-view.tsx 迁入:纯展示、props 驱动。
 */
import { useMemo } from 'react';

import { cn } from './cn.js';
import {
  parseUnifiedDiff,
  sideBySide,
  type DiffRow,
  type SideBySideRow,
} from './diff.js';

const ROW_CLASS: Record<DiffRow['kind'], string> = {
  hunk: 'bg-ha-panel-3 text-ha-faint',
  add: 'bg-ha-diff-add-bg text-ha-diff-add-text',
  del: 'bg-ha-diff-del-bg text-ha-diff-del-text',
  context: 'text-ha-dim',
};

function Gutter({ value }: { value: number | null }) {
  return (
    <span className="w-9 shrink-0 select-none pr-3 text-right text-[11px] text-ha-faint/70">
      {value ?? ''}
    </span>
  );
}

export function DiffRows({
  rows,
  numbers = 'none',
  className,
}: {
  rows: readonly DiffRow[];
  numbers?: 'none' | 'single' | 'double';
  className?: string;
}) {
  return (
    <div className={cn('font-mono text-[12px] leading-[20px]', className)}>
      {rows.map((row, index) => (
        <div
          key={`${row.kind}-${index}-${row.text}`}
          className={cn(
            'flex w-max min-w-full items-start whitespace-pre px-3 py-[3px]',
            ROW_CLASS[row.kind],
          )}
        >
          {numbers === 'double' ? (
            <>
              <Gutter value={row.oldLine} />
              <Gutter value={row.newLine} />
            </>
          ) : numbers === 'single' ? (
            <Gutter value={row.kind === 'del' ? row.oldLine : row.newLine} />
          ) : null}
          <span className="min-w-0 flex-1">{row.text || ' '}</span>
        </div>
      ))}
    </div>
  );
}

export function SideBySideRows({
  rows,
  className,
}: {
  rows: readonly SideBySideRow[];
  className?: string;
}) {
  return (
    <div className={cn('grid grid-cols-2 font-mono text-[12px] leading-[20px]', className)}>
      <div className="border-r border-ha-border">
        {rows.map((row, index) => (
          <div
            key={`left-${index}`}
            className={cn(
              'flex min-h-[26px] items-start whitespace-pre px-3 py-[3px]',
              row.kind === 'hunk'
                ? 'bg-ha-panel-3 text-ha-faint'
                : row.left?.kind === 'del'
                  ? 'bg-ha-diff-del-bg text-ha-diff-del-text'
                  : row.kind === 'context' || row.left
                    ? 'text-ha-dim'
                    : '',
            )}
          >
            <Gutter value={row.left?.oldLine ?? null} />
            <span className="min-w-0">{row.left?.text ?? ''}</span>
          </div>
        ))}
      </div>
      <div>
        {rows.map((row, index) => (
          <div
            key={`right-${index}`}
            className={cn(
              'flex min-h-[26px] items-start whitespace-pre px-3 py-[3px]',
              row.kind === 'hunk'
                ? 'bg-ha-panel-3 text-ha-faint'
                : row.right?.kind === 'add'
                  ? 'bg-ha-diff-add-bg text-ha-diff-add-text'
                  : row.kind === 'context' || row.right
                    ? 'text-ha-dim'
                    : '',
            )}
          >
            <Gutter value={row.right?.newLine ?? null} />
            <span className="min-w-0">{row.right?.text ?? ''}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CodePreview({
  content,
  numbers = true,
  className,
}: {
  content: string;
  numbers?: boolean;
  className?: string;
}) {
  const lines = useMemo(() => content.split('\n'), [content]);
  return (
    <div
      className={cn(
        'overflow-x-auto font-mono text-[12px] leading-[20px] text-ha-text-2',
        className,
      )}
    >
      {lines.map((line, index) => (
        <div
          key={index}
          className="flex w-max min-w-full items-start whitespace-pre px-3 py-[3px] hover:bg-ha-panel-2/40"
        >
          {numbers ? <Gutter value={index + 1} /> : null}
          <span className="min-w-0 flex-1">{line || ' '}</span>
        </div>
      ))}
    </div>
  );
}

export function DiffViewer({
  content,
  view = 'inline',
  numbers = 'none',
  className,
}: {
  content: string;
  view?: 'inline' | 'split';
  numbers?: 'none' | 'single' | 'double';
  className?: string;
}) {
  const rows = useMemo(() => parseUnifiedDiff(content), [content]);
  if (!rows) {
    return <CodePreview content={content} numbers={numbers !== 'none'} className={className} />;
  }
  if (view === 'split') {
    return <SideBySideRows rows={sideBySide(rows)} className={className} />;
  }
  return <DiffRows rows={rows} numbers={numbers} className={className} />;
}
