import type { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';

import { rewriteMessage } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

import { rewriteErrorMessage } from './rewrite-error';
import { createChatStreamHandlers, type ChatToolEvent } from './stream-handlers';
import type { ChatPending } from './use-chat-stream';

/** 编辑重发/重新生成的就地错误:按消息 id 归位到对应气泡。 */
export type ChatRewriteFailure = {
  conversationId: string;
  messageId: string;
  text: string;
};

type UseChatRewriteOptions = {
  activeConversationId: string | null;
  /** 当前 run 的 id:重写成功后失效它(旧 run 可能被替换)。 */
  activeRunId: string | null;
  /** 流式发送/重写共用同一条 busy 标记,期间禁用其它消息操作。 */
  sending: boolean;
  queryClient: QueryClient;
  setManualRun: (run: { conversationId: string; runId: string } | null) => void;
  settle: (conversationId: string) => Promise<void>;
  setPending: Dispatch<SetStateAction<ChatPending | null>>;
  appendToolEvent: (conversationId: string, call: ChatToolEvent) => void;
  setStreamError: (
    value: { conversationId: string; error: Error } | null,
  ) => void;
  setSending: Dispatch<SetStateAction<boolean>>;
  abortRef: MutableRefObject<AbortController | null>;
};

/**
 * 编辑重发 / 重新生成(interaction-closure §2.2):走 rewrite 流,帧处理与发送
 * 完全一致;成功后失效消息、会话列表与当前 run,失败就地中文(不弹全局错误条)。
 * content 省略表示重新生成,有值表示编辑重发。
 */
export function useChatRewrite({
  activeConversationId,
  activeRunId,
  sending,
  queryClient,
  setManualRun,
  settle,
  setPending,
  appendToolEvent,
  setStreamError,
  setSending,
  abortRef,
}: UseChatRewriteOptions) {
  const [rewriteFailure, setRewriteFailure] = useState<ChatRewriteFailure | null>(
    null,
  );

  /** 重写成功后过期当前 run(旧 run 可能被替换)与任务列表。 */
  const refreshRun = async (runId: string | null) => {
    if (runId) {
      await queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
    }
    await queryClient.invalidateQueries({ queryKey: queryKeys.runs });
  };

  const rewrite = async (
    messageId: string,
    userText: string,
    content?: string,
  ): Promise<void> => {
    if (!activeConversationId || sending) return;
    const conversationId = activeConversationId;
    const previousRunId = activeRunId;
    const controller = new AbortController();
    abortRef.current = controller;
    setStreamError(null);
    setRewriteFailure(null);
    setSending(true);
    setPending({
      conversationId,
      user: userText,
      reply: '',
      attachments: [],
      mcpServers: [],
      chatTools: [],
    });
    const fail = (failure: unknown): void => {
      if ((failure as Error)?.name === 'AbortError') return;
      setRewriteFailure({
        conversationId,
        messageId,
        text: rewriteErrorMessage(failure),
      });
    };
    try {
      await rewriteMessage(
        conversationId,
        messageId,
        {
          ...createChatStreamHandlers({
            conversationId,
            queryClient,
            onRun: setManualRun,
            onDelta: (delta) =>
              setPending((previous) =>
                previous && previous.conversationId === conversationId
                  ? { ...previous, reply: previous.reply + delta }
                  : previous,
              ),
            onTool: (call) => appendToolEvent(conversationId, call),
            onDone: () => {
              void settle(conversationId);
              void refreshRun(previousRunId);
            },
            onError: (failure) => {
              // 流内 error 帧:带上 code 走同一套中文映射
              fail(new ApiError(0, failure.code, failure.message));
              void settle(conversationId);
            },
          }),
          signal: controller.signal,
        },
        content,
      );
    } catch (failure) {
      fail(failure);
      void settle(conversationId);
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  return { rewrite, rewriteFailure };
}
