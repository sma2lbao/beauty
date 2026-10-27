import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { ExecutionPanel } from '@/components/console/execution-panel';
import { SandboxFileViewer } from '@/components/console/sandbox-file-viewer';
import type { SandboxViewerTab } from '@/components/console/sandbox-file-viewer';
import { ConsoleStatusBar } from '@/components/console/status-bar';
import { ConsoleTopbar } from '@/components/console/topbar';
import type { SendMode } from '@/lib/conversations-api';
import { isUnauthorized } from '@/lib/runs-api';
import { toSandboxPath } from '@/lib/sandbox-tree';
import { useUiStore } from '@/stores/ui-store';

import { RunFooterAction } from './approval-block';
import { ChatComposer } from './composer';
import { ChatMessageList } from './message-list';
import { useChatActions } from './use-chat-actions';
import { useChatQueries } from './use-chat-queries';
import { useChatStream } from './use-chat-stream';
import {
  useConversationSandbox,
  useSandboxRefresh,
} from './use-conversation-sandbox';

/**
 * 打开中的文件查看器。
 *
 * 带上 conversationId:切换会话时旧浮层自动失效(渲染期判断),
 * 不需要 effect 里 setState。
 */
type OpenFile = {
  conversationId: string;
  path: string;
  tab: SandboxViewerTab;
};

/** 会话工作台页:只做 URL/输入选项与三个域 hook 的装配,外加控制台外壳。 */
export function ChatPage() {
  const [searchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');
  const newFlag = searchParams.get('new') === '1';
  const skillParam = searchParams.get('skill');
  const mcpParam = searchParams.get('mcp');
  const [sendMode, setSendMode] = useState<SendMode>('auto');
  const [skillOverride, setSkillOverride] = useState<string | null>(null);
  const [mcpOverride, setMcpOverride] = useState<string | null>(null);
  const [artifactFor, setArtifactFor] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);

  // 会话选择以 URL 为唯一来源;?skill/&mcp 是默认值,手动切换后以本地选择为准
  const activeConversationId = newFlag ? null : conversationParam;
  const selectedSkill = skillOverride ?? skillParam ?? '';
  const selectedMcp = mcpOverride ?? mcpParam ?? '';
  const queries = useChatQueries(activeConversationId);
  const actions = useChatActions(queries.activeRunId);
  const stream = useChatStream({
    activeConversationId,
    activeWorkspaceId: queries.activeWorkspaceId,
    sendMode,
    selectedSkill,
    selectedMcp,
    mcpServerName: queries.mcpServerName,
    settleConversation: queries.settleConversation,
    setManualRun: queries.setManualRun,
  });
  // 会话沙箱:文件树、终端、文件查看器共用同一份缓存。
  const sandbox = useConversationSandbox(activeConversationId);
  useSandboxRefresh(activeConversationId, queries.toolCalls.length);
  // 通知 watcher 用:声明"用户正盯着的会话",该会话的状态变化只记录不弹 toast
  const setCurrentConversation = useUiStore(
    (state) => state.setCurrentConversation,
  );
  useEffect(() => {
    setCurrentConversation(activeConversationId);
    return () => setCurrentConversation(null);
  }, [activeConversationId, setCurrentConversation]);

  const error = stream.visibleError ?? actions.error ?? queries.queryError;
  const showArtifact =
    Boolean(activeConversationId) && artifactFor === activeConversationId;
  const toggleArtifact = () =>
    setArtifactFor(showArtifact ? null : activeConversationId);

  // 切会话后旧浮层不再显示(不清理状态,等下次打开自然覆盖)。
  const viewer =
    openFile && openFile.conversationId === activeConversationId
      ? openFile
      : null;

  function showFile(path: string, tab: SandboxViewerTab) {
    if (!activeConversationId) return;
    const sandboxPath = toSandboxPath(path);
    if (!sandboxPath) return;
    setOpenFile({ conversationId: activeConversationId, path: sandboxPath, tab });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ConsoleTopbar
        title={queries.title}
        status={queries.activeRunId ? queries.status : null}
        workspace={queries.activeWorkspace?.name ?? null}
      />
      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col bg-ha-bg">
          <ChatMessageList
            queries={queries}
            actions={actions}
            stream={stream}
            activeConversationId={activeConversationId}
            showArtifact={showArtifact}
            onToggleArtifact={toggleArtifact}
            onOpenFile={(path) => showFile(path, 'diff')}
            error={error}
            unauthorized={isUnauthorized(error)}
          />
          {queries.hasNoWorkspaces ? null : (
            <ChatComposer
              queries={queries}
              stream={stream}
              sendMode={sendMode}
              onSendModeChange={setSendMode}
              selectedSkill={selectedSkill}
              onSkillChange={setSkillOverride}
              selectedMcp={selectedMcp}
              onMcpChange={setMcpOverride}
              activeConversationId={activeConversationId}
            />
          )}
        </section>
        <ExecutionPanel
          run={queries.run}
          status={queries.status}
          conversationId={activeConversationId ?? undefined}
          toolCalls={queries.toolCalls}
          steps={queries.steps}
          sandbox={sandbox.data ?? null}
          sandboxLoading={sandbox.isPending}
          sandboxError={sandbox.error instanceof Error ? sandbox.error : null}
          onRetrySandbox={() => {
            void sandbox.refetch();
          }}
          onOpenFile={(path) => showFile(path, 'content')}
          selectedFilePath={viewer?.path ?? null}
          footerAction={
            <RunFooterAction
              run={queries.run}
              status={queries.status}
              busy={actions.busy}
              onApprove={actions.approve}
              onConfirm={actions.confirmDanger}
              onToggleArtifact={toggleArtifact}
            />
          }
        />
      </div>
      {viewer ? (
        <SandboxFileViewer
          conversationId={activeConversationId ?? undefined}
          sandbox={sandbox.data ?? null}
          sandboxLoading={sandbox.isPending}
          sandboxError={sandbox.error instanceof Error ? sandbox.error : null}
          onRetrySandbox={() => {
            void sandbox.refetch();
          }}
          path={viewer.path}
          tab={viewer.tab}
          onTabChange={(tab) => setOpenFile({ ...viewer, tab })}
          toolCalls={queries.toolCalls}
          onClose={() => setOpenFile(null)}
        />
      ) : null}
      <ConsoleStatusBar
        items={queries.statusItems}
        right="beauty moon · 工作台"
      />
    </div>
  );
}
