/**
 * 会话页的文件查看器(右侧浮层,720px)。
 *
 * 两个页签:
 * - 内容:沙箱内该文件的只读文本(带行号);二进制/超大文件只给提示;
 * - 改动:本会话工具调用里针对该文件的 write/edit 参数重建的补丁。
 *
 * 浮层由 chat-page 持有开关(点工具卡路径也走这里),会话切换时随 key 卸载。
 */
import { X } from 'lucide-react';
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';

import { DiffRows, CodePreview } from '@/components/console/diff-view';
import { ConsoleTabs } from '@/components/console/ui';
import { parseUnifiedDiff } from '@/lib/presentation/diff';
import type { ToolCall } from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import type { SandboxInfo } from '@/lib/sandbox-api';
import { readSandboxFile } from '@/lib/sandbox-api';
import { relativeSandboxPath } from '@/lib/sandbox-tree';
import {
  PATCH_TRUNCATED_NOTICE,
  filePatchFromCalls,
} from '@/lib/tool-patch';
import { cn } from '@/lib/utils';

export type SandboxViewerTab = 'content' | 'diff';

/** 内联展示上限:再大就不在会话页里渲染(避免卡浏览器)。 */
const MAX_INLINE_BYTES = 200 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SandboxFileViewer({
  sandbox,
  sandboxLoading,
  sandboxError,
  onRetrySandbox,
  path,
  tab,
  onTabChange,
  toolCalls,
  onClose,
}: {
  sandbox: SandboxInfo | null;
  sandboxLoading: boolean;
  /** 会话沙箱查询失败的原因(有值时给出重试,而不是假装"没有沙箱")。 */
  sandboxError?: Error | null;
  onRetrySandbox?: () => void;
  /** 沙箱内绝对路径。 */
  path: string;
  tab: SandboxViewerTab;
  onTabChange: (tab: SandboxViewerTab) => void;
  toolCalls: readonly ToolCall[];
  onClose: () => void;
}) {
  const relative = relativeSandboxPath(path);
  const name = relative.split('/').pop() || relative || path;

  const file = useQuery({
    queryKey: queryKeys.sandboxFile(sandbox?.id ?? '', path),
    queryFn: () => readSandboxFile((sandbox as SandboxInfo).id, path),
    enabled: Boolean(sandbox) && tab === 'content',
    staleTime: 15_000,
  });

  // Esc 关闭:只挂监听,不改状态以外的东西。
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="文件查看器"
      className="fixed inset-0 z-40 flex justify-end"
    >
      <button
        type="button"
        aria-label="关闭文件查看器"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside className="relative flex h-full w-[720px] max-w-[94vw] flex-col border-l border-ha-border bg-ha-sidebar shadow-2xl">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-ha-border px-4">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-semibold text-ha-text">
              {name}
            </span>
            <span className="block truncate font-mono text-[11px] text-ha-faint">
              {path}
            </span>
          </span>
          <ConsoleTabs
            value={tab}
            onChange={onTabChange}
            items={[
              { value: 'content', label: '内容' },
              { value: 'diff', label: '改动' },
            ]}
          />
          <button
            type="button"
            aria-label="关闭文件查看器"
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {tab === 'content' ? (
            <ContentPane
              sandbox={sandbox}
              sandboxLoading={sandboxLoading}
              sandboxError={sandboxError ?? null}
              onRetrySandbox={onRetrySandbox}
              path={path}
              query={file}
            />
          ) : (
            <DiffPane toolCalls={toolCalls} path={path} />
          )}
        </div>
      </aside>
    </div>
  );
}

function ContentPane({
  sandbox,
  sandboxLoading,
  sandboxError,
  onRetrySandbox,
  path,
  query,
}: {
  sandbox: SandboxInfo | null;
  sandboxLoading: boolean;
  sandboxError: Error | null;
  onRetrySandbox?: () => void;
  path: string;
  query: ReturnType<typeof useQuery<Awaited<ReturnType<typeof readSandboxFile>>>>;
}) {
  if (!sandbox) {
    if (sandboxLoading) return <Hint>正在查找会话沙箱…</Hint>;
    if (sandboxError) {
      return (
        <Hint tone="danger">
          {sandboxError.message}
          {onRetrySandbox ? (
            <button
              type="button"
              onClick={onRetrySandbox}
              className="ml-2 underline decoration-dotted hover:text-ha-text-2"
            >
              重试
            </button>
          ) : null}
        </Hint>
      );
    }
    return <Hint>这个会话还没有运行中的沙箱，暂时读不到文件内容。</Hint>;
  }
  if (query.isPending) {
    return <Hint>读取中…</Hint>;
  }
  if (query.isError) {
    return (
      <Hint tone="danger">
        {query.error instanceof Error ? query.error.message : '文件读取失败'}
        <button
          type="button"
          onClick={() => query.refetch()}
          className="ml-2 underline decoration-dotted hover:text-ha-text-2"
        >
          重试
        </button>
      </Hint>
    );
  }
  const data = query.data;
  if (!data) return <Hint>没有内容。</Hint>;
  if (data.binary) {
    return (
      <Hint>
        这是二进制文件（{formatBytes(data.sizeBytes)}），不支持内联预览。
      </Hint>
    );
  }
  if (data.sizeBytes > MAX_INLINE_BYTES) {
    return (
      <Hint>
        文件较大（{formatBytes(data.sizeBytes)}），超过 200 KB，不在会话页内联展示。
      </Hint>
    );
  }
  if (data.text.length === 0) {
    return <Hint>空文件。</Hint>;
  }
  return (
    <div className="p-3">
      <p className="mb-2 truncate font-mono text-[11px] text-ha-faint">
        {path} · {formatBytes(data.sizeBytes)}
      </p>
      <CodePreview
        content={data.text}
        className="rounded-md border border-ha-border bg-ha-code"
      />
    </div>
  );
}

function DiffPane({
  toolCalls,
  path,
}: {
  toolCalls: readonly ToolCall[];
  path: string;
}) {
  const patch = filePatchFromCalls(toolCalls, path);

  if (patch.status === 'truncated') {
    return (
      <div className="space-y-3 p-4">
        <p className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2 text-[12px] leading-5 text-ha-warning">
          {PATCH_TRUNCATED_NOTICE}
          （工具调用参数超过事件上限会被截断，这里只看得到原文。）
        </p>
        {patch.items.map((item) => (
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

  if (patch.status === 'none') {
    return (
      <Hint>本次会话没有该文件的修改记录。</Hint>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <p className="rounded-md border border-ha-border bg-ha-panel px-3 py-2 text-[11px] leading-5 text-ha-dim">
        补丁由工具调用参数重建；单条参数超过 2000 字符会被截断，仅供参考。
      </p>
      {patch.patches.map((item) => {
        const rows = parseUnifiedDiff(item.diff);
        return (
          <section key={item.seq}>
            <div className="mb-1.5 flex flex-wrap items-center gap-2 font-mono text-[11px]">
              <span className="text-ha-text-2">
                #{item.seq} · {item.toolName}
              </span>
              <span className="truncate text-ha-faint">{item.path}</span>
              {item.note ? (
                <span className="rounded bg-ha-accent-soft px-1.5 py-0.5 text-ha-accent">
                  {item.note}
                </span>
              ) : null}
            </div>
            <div className="overflow-hidden rounded-md border border-ha-border bg-ha-code">
              {rows ? (
                <DiffRows rows={rows} numbers="single" />
              ) : (
                <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-5 text-ha-text-2">
                  {item.diff}
                </pre>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function Hint({
  children,
  tone = 'dim',
}: {
  children: React.ReactNode;
  tone?: 'dim' | 'danger';
}) {
  return (
    <p
      className={cn(
        'm-4 rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5',
        tone === 'danger' ? 'text-ha-danger' : 'text-ha-dim',
      )}
    >
      {children}
    </p>
  );
}
