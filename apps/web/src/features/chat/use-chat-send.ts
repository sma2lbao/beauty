import type { QueryClient } from '@tanstack/react-query';
import type { Dispatch, SetStateAction } from 'react';

import {
  conversationsApi,
  streamMessage,
  type SendMode,
  type StreamMessageHandlers,
} from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

import {
  createChatStreamHandlers,
  type ChatToolEvent,
} from './stream-handlers';
import type { ChatAttachments } from './use-chat-attachments';
import type { ChatContextRef, ChatPending } from './use-chat-stream';

/**
 * 中断收口后补一次消息刷新的延迟:服务端在流结束时(finally)才把已产出片段
 * 落库,客户端看到 EOF 可能早于这次提交,立即重拉会漏掉最后一段。
 */
const INTERRUPTED_REFRESH_MS = 1500;

type MutableRef<T> = { current: T };

type UseChatSendOptions = {
  activeConversationId: string | null;
  activeWorkspaceId: string | null;
  sendMode: SendMode;
  selectedSkill: string;
  selectedMcp: string;
  selectedModel: string;
  selectedConnectors: string[];
  mcpServerName: (id: string) => string;
  connectorName: (provider: string) => string;
  contextRefs: ChatContextRef[];
  setContextRefs: Dispatch<SetStateAction<ChatContextRef[]>>;
  setManualRun: (run: { conversationId: string; runId: string } | null) => void;
  queryClient: QueryClient;
  attachments: ChatAttachments;
  settle: (conversationId: string) => Promise<void>;
  appendToolEvent: (conversationId: string, call: ChatToolEvent) => void;
  setPending: Dispatch<SetStateAction<ChatPending | null>>;
  setStreamError: Dispatch<
    SetStateAction<{ conversationId: string; error: Error } | null>
  >;
  setSending: Dispatch<SetStateAction<boolean>>;
  setDraft: Dispatch<SetStateAction<string>>;
  abortRef: MutableRef<AbortController | null>;
  refreshTimerRef: MutableRef<ReturnType<typeof setTimeout> | null>;
  freshConversationIdRef: MutableRef<string | null>;
  setFreshConversationId: Dispatch<SetStateAction<string | null>>;
};

/**
 * 发送/流式执行:建会话、上传暂存附件、SSE 回调与失败回滚。
 * 从 useChatStream 拆出,保证两个文件都在 400 行以内。
 */
export function useChatSend({
  activeConversationId,
  activeWorkspaceId,
  sendMode,
  selectedSkill,
  selectedMcp,
  selectedModel,
  selectedConnectors,
  mcpServerName,
  connectorName,
  contextRefs,
  setContextRefs,
  setManualRun,
  queryClient,
  attachments,
  settle,
  appendToolEvent,
  setPending,
  setStreamError,
  setSending,
  setDraft,
  abortRef,
  refreshTimerRef,
  freshConversationIdRef,
  setFreshConversationId,
}: UseChatSendOptions) {
  const sendStreaming = async (text: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    // 发送失败要原样还回输入区:附件先记住当前状态,新会话暂存文件由 uploadStaged 补上
    let selectedAttachments = attachments.uploaded;
    // 上一轮中断的延迟刷新若还没触发,先取消:否则会在本轮流中进行时重拉消息,
    // 与服务端的已落库用户消息叠出重复气泡
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    setStreamError(null);
    setSending(true);
    setDraft('');
    let conversationId = activeConversationId;
    // meta 到达即代表用户消息已落库:中断收口据此判断"该重拉"而非"该还草稿"
    let opened = false;
    try {
      if (!conversationId) {
        const created = await conversationsApi.create({
          workspace_id: activeWorkspaceId,
          // 新会话:把输入区选的模型随建会话落库;默认模型不落键
          model: selectedModel || undefined,
        });
        conversationId = created.id;
        // 新会话 id 由 URL effect 写进 URL(单点负责,避免与工作区写回互相覆盖)
        freshConversationIdRef.current = created.id;
        setFreshConversationId(created.id);
      }
      const targetConversationId = conversationId;
      // 新会话的附件暂存在本地:会话建好后才会上传,再把 id 随首条消息发出去
      selectedAttachments =
        await attachments.uploadStaged(targetConversationId);
      attachments.clear();
      const mcpServerIds = selectedMcp ? [selectedMcp] : [];
      setPending({
        conversationId: targetConversationId,
        user: text,
        reply: '',
        attachments: selectedAttachments,
        contextRefs,
        connectors: selectedConnectors.map((id) => ({
          id,
          name: connectorName(id),
        })),
        mcpServers: mcpServerIds.map((id) => ({
          id,
          name: mcpServerName(id),
        })),
        chatTools: [],
      });
      const baseHandlers = createChatStreamHandlers({
        conversationId: targetConversationId,
        queryClient,
        onRun: setManualRun,
        onDelta: (delta) =>
          setPending((previous) =>
            previous && previous.conversationId === targetConversationId
              ? { ...previous, reply: previous.reply + delta }
              : previous,
          ),
        onTool: (call) => appendToolEvent(targetConversationId, call),
        onDone: () => void settle(targetConversationId),
        onError: (failure) => {
          setStreamError({
            conversationId: targetConversationId,
            error: new Error(failure.message),
          });
          void settle(targetConversationId);
        },
      });
      const streamHandlers: StreamMessageHandlers = {
        ...baseHandlers,
        onMeta: (meta) => {
          opened = true;
          baseHandlers.onMeta?.(meta);
        },
        signal: controller.signal,
      };
      const attachmentIds = selectedAttachments.map(
        (attachment) => attachment.id,
      );
      await streamMessage(
        targetConversationId,
        {
          content: text,
          mode: sendMode,
          attachmentIds,
          skillId: selectedSkill || undefined,
          mcpServerIds,
          model: selectedModel || undefined,
          contextRefs: contextRefs.length
            ? contextRefs.map(({ kind, id }) => ({ kind, id }))
            : undefined,
          connectorProviders: selectedConnectors.length
            ? selectedConnectors
            : undefined,
        },
        streamHandlers,
      );
      // 引用已随本轮落库;能力绑定(技能/MCP/连接器)继续保留给下一轮。
      setContextRefs([]);
    } catch (failure) {
      const error = failure instanceof Error ? failure : new Error('发送失败');
      if (error.name === 'AbortError') {
        // 用户点「停止生成」:服务端在 finally 里落库已产出片段,撤掉临时气泡重拉即可
        // 引用已随消息落库,不能留在输入区等下一轮重复引用
        setContextRefs([]);
        if (conversationId) void settle(conversationId);
      } else if (
        opened ||
        (failure instanceof ApiError && failure.code === 'stream.interrupted')
      ) {
        // 流已开(或已判定断线)时用户消息与半截回复都在服务端:不还草稿(重发会
        // 重复),重拉已保存内容;延迟再刷一次兜住服务端落库与客户端 EOF 的竞态。
        // 引用同样已落库,清空输入区的引用轨。
        setContextRefs([]);
        setStreamError({ conversationId: conversationId ?? '', error });
        if (conversationId) {
          const id = conversationId;
          void settle(id);
          if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
          refreshTimerRef.current = setTimeout(() => {
            refreshTimerRef.current = null;
            void queryClient.invalidateQueries({
              queryKey: queryKeys.conversationMessages(id),
            });
          }, INTERRUPTED_REFRESH_MS);
        }
      } else {
        // 开流前就失败(未建 run、未落用户消息):原样还回输入区,便于重发
        setStreamError({ conversationId: conversationId ?? '', error });
        setPending((previous) =>
          previous && previous.conversationId === conversationId
            ? null
            : previous,
        );
        setDraft(text);
        attachments.restore(selectedAttachments);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  return { sendStreaming };
}
