import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import type {
  Attachment,
  SendMode,
  StreamMessageHandlers,
} from '@/lib/conversations-api';
import { conversationsApi, streamMessage } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import type { ToolCallMeta } from '@/lib/tool-meta';

import {
  createChatStreamHandlers,
  type ChatToolEvent,
} from './stream-handlers';
import { useChatAttachments } from './use-chat-attachments';
import { useChatRewrite } from './use-chat-rewrite';

/**
 * 中断收口后补一次消息刷新的延迟:服务端在流结束时(finally)才把已产出片段
 * 落库,客户端看到 EOF 可能早于这次提交,立即重拉会漏掉最后一段。
 */
const INTERRUPTED_REFRESH_MS = 1500;

/** 会话内只读工具调用(由 SSE tool 帧流式累积;P1 富字段全部可缺省)。 */
export type ChatToolCall = {
  name: string;
  arguments: string;
  ok: boolean;
} & ToolCallMeta;

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
  activeRunId,
  sendMode,
  selectedSkill,
  selectedMcp,
  selectedModel,
  mcpServerName,
  settleConversation,
  setManualRun,
}: UseChatStreamOptions) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationParam = searchParams.get('conversation');

  const [draft, setDraft] = useState('');
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
        // 新会话 id 由下方 effect 写进 URL(单点负责,避免与工作区写回互相覆盖)
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
      // 不选的项照旧不传参数,保证原有调用形状(skill 为空、无 MCP 时只有 5 个参数)。
      // 选了模型才把实参补到第 8 位,既有调用形状不变(测试按实参个数断言)。
      const streamArgs: Parameters<typeof streamMessage> = [
        targetConversationId,
        text,
        sendMode,
        streamHandlers,
        attachmentIds,
      ];
      if (selectedModel) {
        streamArgs.push(
          selectedSkill || undefined,
          mcpServerIds.length ? mcpServerIds : undefined,
          selectedModel,
        );
      } else if (mcpServerIds.length) {
        streamArgs.push(selectedSkill || undefined, mcpServerIds);
      } else if (selectedSkill) {
        streamArgs.push(selectedSkill);
      }
      await streamMessage(...streamArgs);
    } catch (failure) {
      const error = failure instanceof Error ? failure : new Error('发送失败');
      if (error.name === 'AbortError') {
        // 用户点「停止生成」:服务端在 finally 里落库已产出片段,撤掉临时气泡重拉即可
        if (conversationId) void settle(conversationId);
      } else if (
        opened ||
        (failure instanceof ApiError && failure.code === 'stream.interrupted')
      ) {
        // 流已开(或已判定断线)时用户消息与半截回复都在服务端:不还草稿(重发会
        // 重复),重拉已保存内容;延迟再刷一次兜住服务端落库与客户端 EOF 的竞态。
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
