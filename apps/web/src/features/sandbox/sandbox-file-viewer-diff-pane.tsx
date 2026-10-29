/**
 * 文件查看器「改动」页:完整 unified diff(或本地重建补丁)的渲染。
 *
 * 含截断提示、「跳到上次修改」的最后一个 section 锚点与增删行数/时间元信息;
 * 数据模型在 ./sandbox-file-viewer-diff-model。
 */
import { useEffect, useRef } from 'react';

import { DiffRows } from '@/components/console/diff-view';
import { diffStats, parseUnifiedDiff } from '@/lib/presentation/diff';
import { PATCH_TRUNCATED_NOTICE } from '@/lib/tool-patch';

import type { DiffView } from './sandbox-file-viewer-diff-model';
import { formatPatchStamp } from './sandbox-file-viewer-diff-model';
import { Hint } from './sandbox-file-viewer-hint';

export function DiffPane({
  view,
  jumpRequest,
}: {
  view: DiffView;
  jumpRequest: number;
}) {
  const lastSection = useRef<HTMLElement | null>(null);

  // 「跳到上次修改」:每次请求 +1,挂到最后一个 section 上滚动过去。
  useEffect(() => {
    if (jumpRequest <= 0) return;
    lastSection.current?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'start',
    });
  }, [jumpRequest]);

  if (view.status === 'truncated') {
    return (
      <div className="space-y-3 p-4">
        <p className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-[12px] leading-5 text-ha-warning">
          {PATCH_TRUNCATED_NOTICE}
          （工具调用参数超过事件上限会被截断，这里只看得到原文。）
        </p>
        {view.items.map((item) => (
          <details
            key={item.seq}
            className="rounded-md border border-ha-border bg-ha-panel"
          >
            <summary className="cursor-pointer px-3 py-2 font-mono text-[11px] text-ha-text-2">
              #{item.seq} {item.toolName} · 原始参数
            </summary>
            <pre className="max-h-72 overflow-auto border-t border-ha-border bg-ha-code p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap break-all text-ha-muted">
              {item.raw}
            </pre>
          </details>
        ))}
      </div>
    );
  }

  if (view.status === 'none') {
    return <Hint>本次会话没有该文件的修改记录。</Hint>;
  }

  return (
    <div className="space-y-4 p-4">
      <p className="rounded-md border border-ha-border bg-ha-panel px-3 py-2 text-[11px] leading-5 text-ha-dim">
        {view.source === 'server'
          ? '服务端补丁 · 完整 unified diff，跨 run 聚合，不受事件 2000 字符截断限制。'
          : '补丁由工具调用参数重建；单条参数超过 2000 字符会被截断，仅供参考。'}
      </p>
      {view.sections.map((section, index) => {
        const rows = parseUnifiedDiff(section.diff);
        const stats = diffStats(section.diff);
        const added = section.linesAdded ?? stats?.added ?? null;
        const removed = section.linesRemoved ?? stats?.removed ?? null;
        const isLast = index === view.sections.length - 1;
        return (
          <section key={section.key} ref={isLast ? lastSection : undefined}>
            <div className="mb-1.5 flex flex-wrap items-center gap-2 font-mono text-[11px]">
              <span className="text-ha-text-2">
                {section.seq === null
                  ? section.toolName
                  : '#' + String(section.seq) + ' · ' + section.toolName}
              </span>
              <span className="truncate text-ha-faint">{section.path}</span>
              {added !== null ? (
                <span className="text-ha-success">{'+' + String(added)}</span>
              ) : null}
              {removed !== null ? (
                <span className="text-ha-danger">{'−' + String(removed)}</span>
              ) : null}
              {section.patchTruncated ? (
                <span className="rounded bg-ha-warning-bg px-1.5 py-0.5 text-ha-warning">
                  补丁超上限已截断
                </span>
              ) : null}
              {section.note ? (
                <span className="rounded bg-ha-accent-soft px-1.5 py-0.5 text-ha-accent">
                  {section.note}
                </span>
              ) : null}
              {section.createdAt ? (
                <time
                  dateTime={section.createdAt}
                  title={section.createdAt}
                  className="text-ha-faint"
                >
                  {formatPatchStamp(section.createdAt)}
                </time>
              ) : null}
            </div>
            <div className="overflow-hidden rounded-md border border-ha-border bg-ha-code">
              {rows ? (
                <DiffRows rows={rows} numbers="single" />
              ) : (
                <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-5 text-ha-text-2">
                  {section.diff}
                </pre>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
