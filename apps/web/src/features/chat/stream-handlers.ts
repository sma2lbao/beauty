/**
 * 会话 SSE 帧处理工厂:发送消息与编辑重发 / 重新生成共用同一套帧语义
 * (meta / delta / tool / done / error),失败分支由调用方决定怎么落地。
 */
import type { QueryClient } from '@tanstack/react-query';

import type { StreamMessageHandlers } from '@/lib/conversations-api';
import { queryKeys } from '@/lib/query-keys';
import type { ToolCallMeta } from '@/lib/tool-meta';

/** 一帧 tool 事件:只读工具调用,或需要人工确认的写工具。 */
export type ChatToolEvent = {
  name: string;
  arguments: string;
  ok: boolean;
} & ToolCallMeta;

export type ChatStreamHandlerOptions = {
  conversationId: string;
  queryClient: QueryClient;
  /** meta 帧带了 run 时通知页面(固定当前 run)。 */
  onRun: (run: { conversationId: string; runId: string }) => void;
  /** delta 帧:追加流式正文。 */
  onDelta: (delta: string) => void;
  /** tool 帧:只读工具流水 / 待确认写工具。 */
  onTool: (call: ChatToolEvent) => void;
  onDone: () => void;
  onError: (error: { code: string; message: string }) => void;
};

/**
 * 生成一轮流式回合的 handlers。会话列表与会话详情在 meta 帧后就失效,
 * 让「新会话/新 run」尽快出现在侧栏与头部,不必等流结束。
 *
 * `signal` 不在工厂里:各调用方自己持有 AbortController。
 */
export function createChatStreamHandlers({
  conversationId,
  queryClient,
  ...events
}: ChatStreamHandlerOptions): StreamMessageHandlers {
  return {
    onMeta: (meta) => {
      if (meta.run) events.onRun({ conversationId, runId: meta.run.id });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversations,
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.conversation(conversationId),
      });
    },
    onDelta: (delta) => events.onDelta(delta),
    onTool: (call) => events.onTool(call),
    onDone: () => events.onDone(),
    onError: (error) => events.onError(error),
  };
}
