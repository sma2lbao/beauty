import { useMemo } from 'react';

import { cn } from '../../lib/utils.js';
import {
  parseUnifiedDiff,
  sideBySide,
  type DiffRow,
  type SideBySideRow,
} from './diff.js';

/**
 * Diff / 代码查看器:工具结果卡与「变更与产物」页共用。
 *
 * 后端没有专门的 diff 字段:正文里如果带 unified diff 标记(---/+++/@@),
 * 就按增删行渲染;否则老老实实回退成带行号的代码预览——不为了好看伪造 diff。
 *
 * 样式是普通 CSS(bd-diff__* / bd-code__* + --bd-* 令牌),增删行颜色取自
 * --bd-success / --bd-destructive 的语义色阶,随宿主主题自动换。
 */

const ROW_MODIFIER: Record<DiffRow['kind'], string> = {
  hunk: 'bd-diff__row--hunk',
  add: 'bd-diff__row--add',
  del: 'bd-diff__row--del',
  context: 'bd-diff__row--context',
};

function Gutter({ value }: { value: number | null }) {
  return (
    <span className="bd-diff__gutter" aria-hidden="true">
      {value ?? ''}
    </span>
  );
}

export interface DiffRowsProps {
  rows: readonly DiffRow[];
  numbers?: 'none' | 'single' | 'double';
  className?: string;
}

export function DiffRows({ rows, numbers = 'none', className }: DiffRowsProps) {
  return (
    <div
      data-slot="diff-rows"
      className={cn('bd-diff', 'bd-diff--inline', className)}
    >
      {rows.map((row, index) => (
        <div
          key={`${row.kind}-${index}-${row.text}`}
          className={cn('bd-diff__row', ROW_MODIFIER[row.kind])}
        >
          {numbers === 'double' ? (
            <>
              <Gutter value={row.oldLine} />
              <Gutter value={row.newLine} />
            </>
          ) : numbers === 'single' ? (
            <Gutter value={row.kind === 'del' ? row.oldLine : row.newLine} />
          ) : null}
          <span className="bd-diff__text">{row.text || ' '}</span>
        </div>
      ))}
    </div>
  );
}

export interface SideBySideRowsProps {
  rows: readonly SideBySideRow[];
  className?: string;
}

export function SideBySideRows({ rows, className }: SideBySideRowsProps) {
  return (
    <div
      data-slot="diff-split"
      className={cn('bd-diff', 'bd-diff--split', className)}
    >
      <div className="bd-diff__side">
        {rows.map((row, index) => (
          <div
            key={`left-${index}`}
            className={cn(
              'bd-diff__cell',
              row.kind === 'hunk'
                ? 'bd-diff__row--hunk'
                : row.left?.kind === 'del'
                  ? 'bd-diff__row--del'
                  : 'bd-diff__row--context',
            )}
          >
            <Gutter value={row.left?.oldLine ?? null} />
            <span className="bd-diff__text">{row.left?.text ?? ''}</span>
          </div>
        ))}
      </div>
      <div className="bd-diff__side bd-diff__side--right">
        {rows.map((row, index) => (
          <div
            key={`right-${index}`}
            className={cn(
              'bd-diff__cell',
              row.kind === 'hunk'
                ? 'bd-diff__row--hunk'
                : row.right?.kind === 'add'
                  ? 'bd-diff__row--add'
                  : 'bd-diff__row--context',
            )}
          >
            <Gutter value={row.right?.newLine ?? null} />
            <span className="bd-diff__text">{row.right?.text ?? ''}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export interface CodePreviewProps {
  content: string;
  numbers?: boolean;
  className?: string;
}

export function CodePreview({
  content,
  numbers = true,
  className,
}: CodePreviewProps) {
  const lines = useMemo(() => content.split('\n'), [content]);
  return (
    <div data-slot="code-preview" className={cn('bd-code', className)}>
      {lines.map((line, index) => (
        <div key={index} className="bd-code__row">
          {numbers ? <Gutter value={index + 1} /> : null}
          <span className="bd-code__text">{line || ' '}</span>
        </div>
      ))}
    </div>
  );
}

export interface DiffViewerProps {
  content: string;
  view?: 'inline' | 'split';
  numbers?: 'none' | 'single' | 'double';
  className?: string;
}

export function DiffViewer({
  content,
  view = 'inline',
  numbers = 'none',
  className,
}: DiffViewerProps) {
  const rows = useMemo(() => parseUnifiedDiff(content), [content]);
  if (!rows) {
    return (
      <CodePreview
        content={content}
        numbers={numbers !== 'none'}
        className={className}
      />
    );
  }
  if (view === 'split') {
    return <SideBySideRows rows={sideBySide(rows)} className={className} />;
  }
  return <DiffRows rows={rows} numbers={numbers} className={className} />;
}
