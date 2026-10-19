/**
 * 工作台(本地电脑模式首页):会话消息流 + 输入框 + 三种空态引导。
 *
 * 数据全部来自 DesktopProvider(settings / workspace / sessions / activeSession),
 * 实时增量由 agent.subscribe 驱动,本页只负责渲染与派发动作:
 * - 未选目录 → 引导「选择本地目录」;
 * - 有目录但没会话 → 引导「新建会话」;
 * - 有会话未选中 → 引导从左侧选择。
 */
import { Empty } from '@beauty/design';
import { FolderOpen, MessageSquarePlus } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { useDesktop } from '@/lib/desktop-context';

import { Composer } from '@/components/composer';
import { MessageStream } from '@/components/message-stream';
import { ConsoleButton } from '@/components/ui';

export function ChatPage() {
  const { state, actions } = useDesktop();
  const workspace = state.workspace;
  const session = state.activeSession;
  const running = Boolean(session && state.runningSessionId === session.id);
  const runError =
    state.runError && state.runError.sessionId === session?.id
      ? state.runError.message
      : null;
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const messageCount = session?.messages.length ?? 0;

  useEffect(() => {
    // 消息增量或运行态变化时贴底。jsdom 里没有 scrollIntoView,用可选调用兜底。
    bottomRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messageCount, running]);

  if (!workspace) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <Empty
          description="本地电脑模式需要先选定一个工作区目录。Agent 的文件读写与命令执行都只在这个目录内进行。"
          actions={
            <ConsoleButton
              variant="primary"
              size="lg"
              onClick={() => void actions.chooseWorkspace()}
            >
              <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
              选择本地目录
            </ConsoleButton>
          }
        />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <Empty
          description={
            state.sessions.length === 0
              ? `工作区「${workspace.name}」已就绪。新建一个会话,让 Agent 读写本机文件、执行命令。`
              : '从左侧会话列表选择一个会话继续,或新建一个会话。'
          }
          actions={
            <ConsoleButton
              variant="primary"
              size="lg"
              onClick={() => void actions.createSession()}
            >
              <MessageSquarePlus aria-hidden="true" className="h-3.5 w-3.5" />
              新建会话
            </ConsoleButton>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto flex w-full max-w-[820px] flex-col gap-6">
          {state.sessionLoading ? (
            <p className="font-mono text-[12px] text-ha-dim">读取会话中…</p>
          ) : null}

          {messageCount === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <span className="grid h-10 w-10 place-items-center rounded-xl border border-ha-border bg-ha-panel-2 text-ha-accent-soft">
                <MessageSquarePlus aria-hidden="true" className="h-4 w-4" />
              </span>
              <p className="mt-1 text-[14px] font-medium text-ha-text-2">
                「{session.title}」已就绪
              </p>
              <p className="max-w-md text-[12px] leading-5 text-ha-dim">
                描述要完成的事。Agent
                可以列出工作区文件、读取内容、写入改动并执行命令。
              </p>
            </div>
          ) : (
            <MessageStream messages={session.messages} running={running} />
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {runError ? (
        <div
          role="alert"
          className="mx-4 mb-2 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] leading-5 text-ha-danger sm:mx-8"
        >
          本次执行失败:{runError}
        </div>
      ) : null}

      <Composer
        running={running}
        onSend={(text) => void actions.sendMessage(text)}
        onCancel={() => void actions.cancelRun()}
      />
    </div>
  );
}
