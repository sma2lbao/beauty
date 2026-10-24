import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import type {
  Attachment,
  SendMode,
  StreamMessageHandlers,
} from '@/lib/conversations-api';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { queryKeys } from '@/lib/query-keys';

/** 会话内只读工具调用(由 SSE tool 帧流式累积)。 */
export type ChatToolCall = {
  name: string;
  arguments: string;
  ok: boolean;
};

/** 流式发送期间临时气泡的内容。 */
export type ChatPending = {
  conversationId: string;
  user: string;
  reply: string;
  attachments: Attachment[];
  /** 本次发送显式引用的 MCP server(展示 chip 用;不选为空数组) */
  mcpServers: { id: string; name: string }[];
  /** 会话内只读工具调用(tool 事件流式累积;done 后随消息一起消失) */
  chatTools: ChatToolCall[];
};

type UseChatStreamOptions = {
  activeConversationId: string | null;
  activeWorkspaceId: string | null;
  sendMode: SendMode;
  selectedSkill: string;
  selectedMcp: string;
  mcpServerName: (id: string) => string;
  /** 失效会话消息/附件/会话列表(由数据层提供)。 */
  settleConversation: (conversationId: string) => Promise<void>;
  setManualRun: (run: { conversationId: string; runId: string } | null) => void;
};

/**
 * 会话工作台的发送/流式层:草稿、附件上传、SSE 流式发送与瞬时错误。
 * 新会话 id 由这里创建,并通过 URL effect 单点写回(?conversation=)。
 */
export function useChatStream({
  activeConversationId,
  activeWorkspaceId,
  sendMode,
  selectedSkill,
  selectedMcp,
  mcpServerName,
  settleConversation,
  setManualRun,
}: UseChatStreamOptions) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');

  const [draft, setDraft] = useState('');
  const [draftAttachments, setDraftAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 瞬时态都带上会话 id:切走再切回不会把上一个会话的流/错误/气泡串过来
  const [pending, setPending] = useState<ChatPending | null>(null);
  const [streamError, setStreamError] = useState<{
    conversationId: string;
    error: Error;
  } | null>(null);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 新会话创建后把 id 写进 URL。两个约束:
  // 1) 必须在 effect 里:useSearchParams 的函数式 updater 读的是"当次渲染"的
  //    searchParams,异步回调里读到的是发送前的旧值,会与"默认工作区写回 URL"
  //    的 effect 互相覆盖(表现为新会话冒出来又消失);
  // 2) 用独立 state 记住这个 id 而不是 pending——流很快结束时 pending 会被清掉,
  //    此时若 URL 被同批渲染里另一个 effect 覆盖,就没机会补回来。
  // effect 每次渲染都拿到最新 searchParams,双方各补自己缺的参数,最多两轮收敛。
  const [freshConversationId, setFreshConversationId] = useState<string | null>(
    null,
  );
  // 待写标记放 ref:URL 追上后只需清标记,不必在 effect 里 setState
  const freshConversationIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!freshConversationId) {
      return;
    }
    if (freshConversationIdRef.current !== freshConversationId) {
      return;
    }
    if (conversationParam === freshConversationId) {
      freshConversationIdRef.current = null;
      return;
    }
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (activeWorkspaceId) next.set('workspace', activeWorkspaceId);
        next.set('conversation', freshConversationId);
        next.delete('new');
        return next;
      },
      { replace: true },
    );
  }, [
    activeWorkspaceId,
    conversationParam,
    freshConversationId,
    setSearchParams,
  ]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const visiblePending =
    pending && pending.conversationId === activeConversationId ? pending : null;
  const visibleError =
    streamError &&
    (streamError.conversationId === activeConversationId ||
      streamError.conversationId === '')
      ? streamError.error
      : null;

  const settle = async (conversationId: string) => {
    await settleConversation(conversationId);
    setPending((previous) =>
      previous && previous.conversationId === conversationId ? null : previous,
    );
  };

  const uploadAttachments = async (files: FileList | null) => {
    if (!files?.length || !activeConversationId) return;
    setUploading(true);
    setAttachmentError(null);
    const selected = [...draftAttachments];
    try {
      for (const file of Array.from(files)) {
        if (selected.length >= 5) break;
        if (file.size > 10 * 1024 * 1024) {
          throw new Error('附件超过 10MB 上限');
        }
        selected.push(
          await conversationsApi.uploadAttachment(activeConversationId, file),
        );
      }
      setDraftAttachments(selected);
    } catch (failure) {
      setAttachmentError(
        failure instanceof Error ? failure.message : '附件上传失败',
      );
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const sendStreaming = async (text: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    const selectedAttachments = draftAttachments;
    setStreamError(null);
    setSending(true);
    setDraft('');
    setDraftAttachments([]);
    let conversationId = activeConversationId;
    try {
      if (!conversationId) {
        const created = await conversationsApi.create({
          workspace_id: activeWorkspaceId,
        });
        conversationId = created.id;
        // 新会话 id 由下方 effect 写进 URL(单点负责,避免与工作区写回互相覆盖)
        freshConversationIdRef.current = created.id;
        setFreshConversationId(created.id);
      }
      const targetConversationId = conversationId;
      const mcpServerIds = selectedMcp ? [selectedMcp] : [];
      setPending({
        conversationId: targetConversationId,
        user: text,
        reply: '',
        attachments: selectedAttachments,
        mcpServers: mcpServerIds.map((id) => ({
          id,
          name: mcpServerName(id),
        })),
        chatTools: [],
      });
      const streamHandlers: StreamMessageHandlers = {
        signal: controller.signal,
        onMeta: (meta) => {
          if (meta.run) {
            setManualRun({
              conversationId: targetConversationId,
              runId: meta.run.id,
            });
          }
          void queryClient.invalidateQueries({
            queryKey: queryKeys.conversations,
          });
          void queryClient.invalidateQueries({
            queryKey: queryKeys.conversation(targetConversationId),
          });
        },
        onDelta: (delta) =>
          setPending((previous) =>
            previous && previous.conversationId === targetConversationId
              ? { ...previous, reply: previous.reply + delta }
              : previous,
          ),
        onTool: (call) =>
          setPending((previous) =>
            previous && previous.conversationId === targetConversationId
              ? { ...previous, chatTools: [...previous.chatTools, call] }
              : previous,
          ),
        onDone: () => {
          void settle(targetConversationId);
        },
        onError: (failure) => {
          setStreamError({
            conversationId: targetConversationId,
            error: new Error(failure.message),
          });
          void settle(targetConversationId);
        },
      };
      const attachmentIds = selectedAttachments.map(
        (attachment) => attachment.id,
      );
      // 不选的项照旧不传参数,保证原有调用形状(skill 为空、无 MCP 时只有 5 个参数)
      if (mcpServerIds.length) {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
          selectedSkill || undefined,
          mcpServerIds,
        );
      } else if (selectedSkill) {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
          selectedSkill,
        );
      } else {
        await streamMessage(
          targetConversationId,
          text,
          sendMode,
          streamHandlers,
          attachmentIds,
        );
      }
    } catch (failure) {
      if ((failure as Error)?.name !== 'AbortError') {
        const error =
          failure instanceof Error ? failure : new Error('发送失败');
        setStreamError({ conversationId: conversationId ?? '', error });
        setDraft(text);
        setDraftAttachments(selectedAttachments);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  const canSend = draft.trim().length > 0 && !sending;
  const submit = () => {
    const text = draft.trim();
    if (!text || sending) return;
    void sendStreaming(text);
  };
  const stop = () => abortRef.current?.abort();
  const removeAttachment = (attachmentId: string) =>
    setDraftAttachments((previous) =>
      previous.filter((item) => item.id !== attachmentId),
    );

  return {
    draft,
    setDraft,
    draftAttachments,
    removeAttachment,
    uploading,
    attachmentError,
    fileInputRef,
    uploadAttachments,
    sending,
    canSend,
    submit,
    stop,
    visiblePending,
    visibleError,
    sendStreaming,
  };
}

export type ChatStream = ReturnType<typeof useChatStream>;
