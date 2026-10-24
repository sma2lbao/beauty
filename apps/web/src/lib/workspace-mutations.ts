/**
 * 工作区写操作(重命名/删除):会话页、侧栏、工作区页共用。
 *
 * 删除/改名会影响多处缓存,所以集中在这里统一失效,避免每个入口各写一套:
 * - ['workspaces']:工作区清单与默认工作区;
 * - ['conversations'] / ['conversation']:侧栏分组、会话详情(工作区名/归属);
 * - ['workspace-artifacts'] / ['artifacts']:工作区页与交付物预览。
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/lib/http';
import { workspaceApi } from '@/lib/workspace-api';
import { WORKSPACES_KEY } from '@/lib/workspace-selection';
import { queryKeys } from '@/lib/query-keys';

/** 后端错误码 → 控制台可读文案(未知错误保留后端 message)。 */
export function workspaceErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'workspace.exists') {
      return '同名工作区已存在';
    }
    if (error.code === 'workspace.default_protected') {
      return '默认工作区由系统维护,不能删除';
    }
    if (error.code === 'workspace.not_found') {
      return '工作区不存在,可能已被删除';
    }
    return error.message;
  }
  return '操作失败,请稍后再试。';
}

export function useWorkspaceMutations() {
  const queryClient = useQueryClient();

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY }),
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations }),
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationPrefix }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.workspaceArtifactsAll,
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.artifacts }),
    ]);
  };

  const rename = useMutation({
    mutationFn: ({
      workspaceId,
      name,
    }: {
      workspaceId: string;
      name: string;
    }) => workspaceApi.renameWorkspace(workspaceId, { name }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (workspaceId: string) =>
      workspaceApi.removeWorkspace(workspaceId),
    onSuccess: invalidate,
  });

  return { rename, remove };
}
