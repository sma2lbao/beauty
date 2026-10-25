import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { ExecutionPanel } from '@/components/console/execution-panel';
import { ConsoleStatusBar } from '@/components/console/status-bar';
import { ConsoleTopbar } from '@/components/console/topbar';
import type { SendMode } from '@/lib/conversations-api';
import { isUnauthorized } from '@/lib/runs-api';
import { useUiStore } from '@/stores/ui-store';

import { RunFooterAction } from './approval-block';
import { ChatComposer } from './composer';
import { ChatMessageList } from './message-list';
import { useChatActions } from './use-chat-actions';
import { useChatQueries } from './use-chat-queries';
import { useChatStream } from './use-chat-stream';

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
          events={queries.events}
          toolCalls={queries.toolCalls}
          steps={queries.steps}
          connected={queries.connected}
          reconnecting={queries.reconnecting}
          runId={queries.activeRunId}
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
      <ConsoleStatusBar
        items={queries.statusItems}
        right="beauty moon · 工作台"
      />
    </div>
  );
}
