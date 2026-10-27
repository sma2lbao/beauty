/**
 * 会话沙箱查询:执行面板的「文件 / 终端」页签共用。
 *
 * 键统一走 queryKeys.conversationSandbox,页面与面板各取一份也只会发一次请求。
 * 不给会话时禁用查询(新建会话页没有沙箱,不该报错)。
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { queryKeys } from '@/lib/query-keys';
import { conversationSandbox } from '@/lib/sandbox-api';

export function useConversationSandbox(conversationId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.conversationSandbox(conversationId ?? ''),
    queryFn: () => conversationSandbox(conversationId as string),
    enabled: Boolean(conversationId),
    staleTime: 15_000,
    retry: 1,
  });
}

/**
 * 工具调用变多后刷新沙箱与文件缓存(不开轮询)。
 *
 * 只认「数量变化」:同一次事件的重复投递、SSE 重连不会触发额外请求;
 * 首次挂载也算基线,不产生一轮多余失效。
 */
export function useSandboxRefresh(
  conversationId: string | null | undefined,
  toolCallCount: number,
) {
  const queryClient = useQueryClient();
  const lastCount = useRef(toolCallCount);

  useEffect(() => {
    if (lastCount.current === toolCallCount) return;
    lastCount.current = toolCallCount;
    if (!conversationId) return;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.conversationSandbox(conversationId),
    });
    void queryClient.invalidateQueries({ queryKey: ['sandbox-tree'] });
    void queryClient.invalidateQueries({ queryKey: ['sandbox-file'] });
  }, [conversationId, queryClient, toolCallCount]);
}
