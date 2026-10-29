/**
 * 文件查看器「内容」页:沙箱文件读取结果的渲染——正文(带行号)或二进制 /
 * 超大 / 空文件的实话实说提示。
 */
import { CodePreview } from '@/components/console/diff-view';
import { formatBytes } from '@/lib/presentation';
import type { SandboxInfo } from '@/lib/sandbox-api';

import { Hint } from './sandbox-file-viewer-hint';
import type { SandboxFileContentQuery } from './use-sandbox-file-content';

/** 内联展示上限:再大就不在会话页里渲染(避免卡浏览器)。 */
const MAX_INLINE_BYTES = 200 * 1024;

export function ContentPane({
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
  query: SandboxFileContentQuery;
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
        文件较大（{formatBytes(data.sizeBytes)}），超过 200
        KB，不在会话页内联展示。
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
