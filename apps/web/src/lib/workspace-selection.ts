/**
 * 工作区选择:URL 的 ?workspace=<id> 是唯一真源。
 *
 * 控制台的侧栏、会话页、工作区页共用这一份状态:
 * - 缺省(URL 没有 workspace)时回落到租户默认工作区,并把 id 写回 URL(replace,不产生历史);
 * - 切换工作区只是改 URL,页面通过 useSearchParams 自然重渲染,刷新/分享都还原同一视图;
 * - 切换工作区默认丢弃当前会话参数(conversation/new),避免把 A 工作区的会话挂到 B 工作区;
 *   按会话归属对齐 URL 的场景(旧链接/详情返回)用 keepConversation。
 */
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router';

import { queryKeys } from '@/lib/query-keys';
import { workspaceApi, type Workspace } from '@/lib/workspace-api';

/** 工作区列表 + 默认工作区的缓存键(建工作区后往这里补数据);键定义收敛在 @/lib/query-keys。 */
export const WORKSPACES_KEY = queryKeys.workspaces;

export type WorkspaceList = {
  workspaces: Workspace[];
  defaultWorkspace: Workspace | null;
};

/** 工作区清单:并行取清单与默认工作区;清单为空时用默认工作区兜底。 */
export function useWorkspaceList() {
  return useQuery<WorkspaceList>({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => {
      const [workspaces, defaultWorkspace] = await Promise.all([
        workspaceApi.workspaces(),
        workspaceApi.defaultWorkspace(),
      ]);
      // 清单为空时用默认工作区兜底;两者都空就是「还没有工作区」,
      // 不要塞 null 进数组(hook 的 find 会炸)。
      return {
        workspaces:
          workspaces.length > 0
            ? workspaces
            : defaultWorkspace
              ? [defaultWorkspace]
              : [],
        defaultWorkspace,
      };
    },
  });
}

export type WorkspaceSelection = {
  workspaces: Workspace[];
  defaultWorkspace: Workspace | null;
  activeWorkspace: Workspace | null;
  activeWorkspaceId: string | null;
  isLoading: boolean;
  error: unknown;
  /** 切换工作区:只改 URL(默认丢弃会话参数)。 */
  selectWorkspace: (
    workspaceId: string,
    options?: { keepConversation?: boolean },
  ) => void;
};

/**
 * 当前工作区选择。activeWorkspaceId 的来源优先级:
 * URL ?workspace > 租户默认工作区 > 清单第一个。
 */
export function useWorkspaceSelection(): WorkspaceSelection {
  const listQuery = useWorkspaceList();
  const [searchParams, setSearchParams] = useSearchParams();

  // 冻结引用,避免 fallback effect 因数组字面量每轮渲染都变而反复触发。
  const workspaces = useMemo(
    () => listQuery.data?.workspaces ?? [],
    [listQuery.data],
  );
  const defaultWorkspace = useMemo(
    () => listQuery.data?.defaultWorkspace ?? null,
    [listQuery.data],
  );
  const paramWorkspaceId = searchParams.get('workspace');
  const activeWorkspaceId =
    paramWorkspaceId ?? defaultWorkspace?.id ?? workspaces[0]?.id ?? null;
  const activeWorkspace =
    workspaces.find((workspace) => workspace.id === activeWorkspaceId) ?? null;

  // 默认工作区解析出来后写回 URL:让地址栏始终可复制、可刷新。
  useEffect(() => {
    if (searchParams.get('workspace') || !activeWorkspaceId) {
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('workspace', activeWorkspaceId);
        return next;
      },
      { replace: true },
    );
  }, [activeWorkspaceId, searchParams, setSearchParams]);

  // URL 里的工作区已不存在(被删除/权限收回/旧链接)时回落到默认或第一个:
  // 否则页面会停在「当前没有工作区」,用户还得手动切一次。
  useEffect(() => {
    if (listQuery.isLoading || !paramWorkspaceId) {
      return;
    }
    if (workspaces.length === 0) {
      return;
    }
    if (workspaces.some((workspace) => workspace.id === paramWorkspaceId)) {
      return;
    }
    const fallbackId = defaultWorkspace?.id ?? workspaces[0]?.id;
    if (!fallbackId) {
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('workspace', fallbackId);
        return next;
      },
      { replace: true },
    );
  }, [
    defaultWorkspace,
    listQuery.isLoading,
    paramWorkspaceId,
    setSearchParams,
    workspaces,
  ]);

  const selectWorkspace = useCallback(
    (workspaceId: string, options?: { keepConversation?: boolean }) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('workspace', workspaceId);
          if (!options?.keepConversation) {
            next.delete('conversation');
            next.delete('new');
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return {
    workspaces,
    defaultWorkspace,
    activeWorkspace,
    activeWorkspaceId,
    isLoading: listQuery.isLoading,
    error: listQuery.error,
    selectWorkspace,
  };
}
