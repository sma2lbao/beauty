import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import type {
  Attachment,
  ContextRefInput,
  SendMode,
} from '@/lib/conversations-api';
import type { ToolCallMeta } from '@/lib/tool-meta';

import type { ChatToolEvent } from './stream-handlers';
import { useChatAttachments } from './use-chat-attachments';
import { useChatRewrite } from './use-chat-rewrite';
import { useChatSend } from './use-chat-send';

/** 会话内只读工具调用(由 SSE tool 帧流式累积;P1 富字段全部可缺省)。 */
export type ChatToolCall = {
  name: string;
  arguments: string;
  ok: boolean;
} & ToolCallMeta;

/** 本轮输入区挂上的内容引用(带本地标题,展示芯片用)。 */
export type ChatContextRef = ContextRefInput & { title?: string };

/** 流式发送期间临时气泡的内容。 */
export type ChatPending = {
  conversationId: string;
  user: string;
  reply: string;
  attachments: Attachment[];
  /** 本次发送显式引用的内容资料(展示 chip 用;不选为空数组) */
  contextRefs: ChatContextRef[];
  /** 本次发送显式引用的连接器 provider(展示 chip 用;不选为空数组) */
  connectors: { id: string; name: string }[];
  /** 本次发送显式引用的 MCP server(展示 chip 用;不选为空数组) */
  mcpServers: { id: string; name: string }[];
  /** 会话内只读工具调用(tool 事件流式累积;done 后随消息一起消失) */
  chatTools: ChatToolCall[];
};

/**
 * 待人工确认的写工具调用(按会话粘住)。
 * 这类帧不能放进 pending:onDone 会清掉临时气泡,确认按钮就消失了;
 * 因此单独存一份,直到用户确认/取消(或服务端返回 409)。
 */
export type ChatToolConfirmation = {
  conversationId: string;
  call: ChatToolCall;
};

type UseChatStreamOptions = {
  activeConversationId: string | null;
  activeWorkspaceId: string | null;
  /** 当前 run 的 id:重写成功后失效它(切会话/切 run 由页面传最新值)。 */
  activeRunId: string | null;
  sendMode: SendMode;
  selectedSkill: string;
  selectedMcp: string;
  /**
   * 本轮使用的模型(C1 §6.3):空串 = 默认模型,不往请求里带,
   * 沿用会话默认/网关默认;非空时随建会话与流式消息一起带上。
   */
  selectedModel: string;
  /** 本轮显式引用的连接器 provider(能力绑定,来自页面状态)。 */
  selectedConnectors: string[];
  mcpServerName: (id: string) => string;
  connectorName: (provider: string) => string;
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
  activeRunId,
  sendMode,
  selectedSkill,
  selectedMcp,
  selectedModel,
  selectedConnectors,
  mcpServerName,
  connectorName,
  settleConversation,
  setManualRun,
}: UseChatStreamOptions) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');

  const [draft, setDraft] = useState('');
  // 内容引用(知识库/交付物/沙箱文件):正文 token 只是可读回声,
  // 提交时只发结构化 kind+id;开流前失败保留,发送成功后清空。
  const [contextRefs, setContextRefs] = useState<ChatContextRef[]>([]);
  const addContextRef = (ref: ChatContextRef) =>
    setContextRefs((previous) =>
      previous.some((item) => item.kind === ref.kind && item.id === ref.id)
        ? previous
        : [...previous, ref],
    );
  const removeContextRef = (ref: ContextRefInput) =>
    setContextRefs((previous) =>
      previous.filter(
        (item) => !(item.kind === ref.kind && item.id === ref.id),
      ),
    );
  // 附件选择/上传/暂存(新会话没有 id 时先暂存,发送时随建会话上传)
  const attachments = useChatAttachments(activeConversationId);
  // 瞬时态都带上会话 id:切走再切回不会把上一个会话的流/错误/气泡串过来
  const [pending, setPending] = useState<ChatPending | null>(null);
  // 待确认的写工具:跨流存活,仅在用户确认/取消/失效后仍留在列表里展示结果
  const [toolConfirmations, setToolConfirmations] = useState<
    ChatToolConfirmation[]
  >([]);
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

  /** 中断收口的延迟刷新句柄;卸载时清掉,避免对已销毁页面重复取数。 */
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    },
    [],
  );

  const visiblePending =
    pending && pending.conversationId === activeConversationId ? pending : null;
  const visibleConfirmations = activeConversationId
    ? toolConfirmations.filter(
        (item) => item.conversationId === activeConversationId,
      )
    : [];
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

  /**
   * tool 帧:待确认的写工具常驻(同一 confirmation 只登记一次,避免重连重放
   * 出现两张确认卡),其余只读工具进临时气泡的流水。
   */
  const appendToolEvent = (
    conversationId: string,
    call: ChatToolEvent,
  ): void => {
    if (call.confirmationId) {
      setToolConfirmations((previous) =>
        previous.some(
          (item) =>
            item.conversationId === conversationId &&
            item.call.confirmationId === call.confirmationId,
        )
          ? previous
          : [...previous, { conversationId, call }],
      );
      return;
    }
    setPending((previous) =>
      previous && previous.conversationId === conversationId
        ? { ...previous, chatTools: [...previous.chatTools, call] }
        : previous,
    );
  };

  // 编辑重发/重新生成:错误与流处理抽在 useChatRewrite(本文件守 400 行上限)
  const { rewrite, rewriteFailure } = useChatRewrite({
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
  });
  const visibleRewriteError =
    rewriteFailure && rewriteFailure.conversationId === activeConversationId
      ? rewriteFailure
      : null;

  const { sendStreaming } = useChatSend({
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
  });

  const canSend = draft.trim().length > 0 && !sending;
  const submit = () => {
    const text = draft.trim();
    if (!text || sending) return;
    void sendStreaming(text);
  };
  const stop = () => abortRef.current?.abort();

  return {
    draft,
    setDraft,
    attachments,
    contextRefs,
    addContextRef,
    removeContextRef,
    setContextRefs,
    sending,
    canSend,
    submit,
    stop,
    visiblePending,
    visibleConfirmations,
    visibleError,
    sendStreaming,
    rewrite,
    visibleRewriteError,
  };
}

export type ChatStream = ReturnType<typeof useChatStream>;
