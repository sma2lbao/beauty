/**
 * 会话页的文件查看器(右侧浮层,720px)。
 *
 * 两个页签:
 * - 内容:沙箱内该文件的只读文本(带行号);二进制/超大文件只给提示;
 * - 改动:优先展示服务端 tool-calls 审计回传的**完整** unified diff(跨 run
 *   聚合、不受事件 2000 字符截断限制);端点降级(degraded)或没有可用补丁时,
 *   回退到本会话工具调用参数重建(见 lib/tool-patch)。
 *
 * 「跳到上次修改」把用户直接带到最后一个补丁 section:当前在「内容」页签时
 * 先切到「改动」,再滚动到最后一节(没有补丁时按钮禁用并说明原因)。
 *
 * 浮层由 chat-page 持有开关(点工具卡路径也走这里),会话切换时随 key 卸载。
 */
import { ArrowDownToLine, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { DiffRows, CodePreview } from '@/components/console/diff-view';
import { ConsoleTabs } from '@/components/console/ui';
import { diffStats, parseUnifiedDiff } from '@/lib/presentation/diff';
import type { ToolCall } from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import type { SandboxInfo } from '@/lib/sandbox-api';
import { readSandboxFile } from '@/lib/sandbox-api';
import { relativeSandboxPath } from '@/lib/sandbox-tree';
import type {
  ConversationToolCallPage,
  FilePatchItem,
} from '@/lib/tool-calls-api';
import {
  filePatchesFromCalls,
  useConversationToolCalls,
} from '@/lib/tool-calls-api';
import {
  PATCH_TRUNCATED_NOTICE,
  filePatchFromCalls,
  type TruncatedPatch,
} from '@/lib/tool-patch';
import { cn } from '@/lib/utils';

export type SandboxViewerTab = 'content' | 'diff';

/** 内联展示上限:再大就不在会话页里渲染(避免卡浏览器)。 */
const MAX_INLINE_BYTES = 200 * 1024;

/** 查看器可见时的补丁轮询间隔:工具还在写文件,追加的改动要能自己出现。 */
const PATCH_REFETCH_MS = 15_000;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return String(bytes) + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

/** ISO 时间 → 本地「月/日 时:分」;非法/缺失时间原样返回,不显示 Invalid Date。 */
function formatStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** 补丁展示的一节(服务端与本地来源统一成同一形状)。 */
type DiffSection = {
  key: string;
  /** 事件/工具调用的 seq;服务端来源没有 seq(null)。 */
  seq: number | null;
  toolName: string;
  path: string;
  diff: string;
  note: string | null;
  linesAdded: number | null;
  linesRemoved: number | null;
  patchTruncated: boolean;
  createdAt: string | null;
};

type DiffView =
  | { status: 'ok'; source: 'server' | 'local'; sections: DiffSection[] }
  | { status: 'truncated'; items: TruncatedPatch[] }
  | { status: 'none' };

/**
 * 解析该文件该看哪些补丁:服务端优先,不可用时回退本地重建。
 *
 * - 服务端(degraded=false)有命中项 → 完整 diff;
 * - degraded / 端点失败 / 无命中 → 本地重建,截断时保留原有提示;
 * - 两者都没有 → none。
 */
function buildDiffView(
  server: ConversationToolCallPage | undefined,
  toolCalls: readonly ToolCall[],
  path: string,
): DiffView {
  const remote: FilePatchItem[] = filePatchesFromCalls(server, path).items;
  if (remote.length > 0) {
    return {
      status: 'ok',
      source: 'server',
      sections: remote.map((item, index) => ({
        key: 'server-' + String(index),
        seq: null,
        toolName: item.toolName,
        path: item.path,
        diff: item.patch,
        note: null,
        linesAdded: item.linesAdded,
        linesRemoved: item.linesRemoved,
        patchTruncated: item.patchTruncated,
        createdAt: item.createdAt || null,
      })),
    };
  }

  const local = filePatchFromCalls(toolCalls, path);
  if (local.status === 'ok') {
    return {
      status: 'ok',
      source: 'local',
      sections: local.patches.map((item) => ({
        key: 'local-' + String(item.seq),
        seq: item.seq,
        toolName: item.toolName,
        path: item.path,
        diff: item.diff,
        note: item.note,
        linesAdded: null,
        linesRemoved: null,
        patchTruncated: false,
        createdAt: item.occurredAt ?? null,
      })),
    };
  }
  if (local.status === 'truncated') {
    return { status: 'truncated', items: local.items };
  }
  return { status: 'none' };
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
  conversationId,
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
  /** 会话 id:有值时「改动」页签优先用服务端补丁;缺省时行为与旧版一致。 */
  conversationId?: string;
  onClose: () => void;
}) {
  const relative = relativeSandboxPath(path);
  const name = relative.split('/').pop() || relative || path;

  const [jumpRequest, setJumpRequest] = useState(0);

  const file = useQuery({
    queryKey: queryKeys.sandboxFile(sandbox?.id ?? '', path),
    queryFn: () => readSandboxFile((sandbox as SandboxInfo).id, path),
    enabled: Boolean(sandbox) && tab === 'content',
    staleTime: 15_000,
  });

  // 浮层打开且有会话 id 时取一次服务端补丁(「内容」页签也要能判断
  // 「跳到上次修改」是否可用);只在「改动」页签可见时随工具继续写而 15s 自刷新。
  const remote = useConversationToolCalls(conversationId, {
    refetchInterval: tab === 'diff' ? PATCH_REFETCH_MS : false,
  });

  const view = useMemo(
    () => buildDiffView(remote.data, toolCalls, path),
    [remote.data, toolCalls, path],
  );

  const canJump = view.status === 'ok' && view.sections.length > 0;

  function jumpToLastChange() {
    if (!canJump) return;
    if (tab !== 'diff') onTabChange('diff');
    setJumpRequest((current) => current + 1);
  }

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
            aria-label="跳到上次修改"
            title={
              canJump
                ? '滚动到该文件最后一个补丁'
                : '本次会话还没有该文件的补丁，无法跳转'
            }
            onClick={jumpToLastChange}
            disabled={!canJump}
            className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ha-muted"
          >
            <ArrowDownToLine aria-hidden="true" className="h-4 w-4" />
          </button>
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
            <DiffPane view={view} jumpRequest={jumpRequest} />
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
    lastSection.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
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
                  {formatStamp(section.createdAt)}
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
