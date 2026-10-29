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
 *
 * 实现拆分:补丁模型在 ./sandbox-file-viewer-diff-model,顶栏在
 * ./sandbox-file-viewer-header,内容页在 ./sandbox-file-viewer-content-pane,
 * 改动页在 ./sandbox-file-viewer-diff-pane;本文件只做浮层外壳与状态编排。
 */
import { useEffect, useMemo, useState } from 'react';

import type { ToolCall } from '@/lib/presentation';
import type { SandboxInfo } from '@/lib/sandbox-api';
import { relativeSandboxPath } from '@/lib/sandbox-tree';
import { useConversationToolCalls } from '@/lib/tool-calls-api';

import { ContentPane } from './sandbox-file-viewer-content-pane';
import { buildDiffView } from './sandbox-file-viewer-diff-model';
import { DiffPane } from './sandbox-file-viewer-diff-pane';
import { SandboxFileViewerHeader } from './sandbox-file-viewer-header';
import { useSandboxFileContent } from './use-sandbox-file-content';

export type SandboxViewerTab = 'content' | 'diff';

/** 查看器可见时的补丁轮询间隔:工具还在写文件,追加的改动要能自己出现。 */
const PATCH_REFETCH_MS = 15_000;

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

  const file = useSandboxFileContent(sandbox, path, tab === 'content');

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
        <SandboxFileViewerHeader
          name={name}
          path={path}
          tab={tab}
          onTabChange={onTabChange}
          canJump={canJump}
          onJumpToLastChange={jumpToLastChange}
          onClose={onClose}
        />

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
