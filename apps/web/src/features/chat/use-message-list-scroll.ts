/**
 * 消息流滚动跟随:新消息 / 新事件 / 新工具卡 / 正文增长 / 提问状态变化时,
 * 把消息流底部滚动到最近可见位置。依赖项显式列出,便于页面按需传入。
 */
import { useEffect, useRef } from 'react';

export function useMessageListScroll({
  messagesCount,
  eventLinesCount,
  toolCallsCount,
  liveRunTextLength,
  pendingReplyLength,
  questionPhase,
  questionId,
}: {
  messagesCount: number;
  eventLinesCount: number;
  toolCallsCount: number;
  liveRunTextLength: number | undefined;
  pendingReplyLength: number | undefined;
  questionPhase: string | undefined;
  questionId: string | undefined;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'nearest' });
  }, [
    messagesCount,
    eventLinesCount,
    toolCallsCount,
    liveRunTextLength,
    pendingReplyLength,
    questionPhase,
    questionId,
  ]);

  return bottomRef;
}
