/**
 * 控制台侧边栏的数据模型:工作区/会话查询、搜索、归档视图与就地操作。
 *
 * 侧边栏组件只保留布局(R-18),状态、取数与派生逻辑集中在这里,
 * 由 components/console/sidebar.tsx 调用。
 */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useDeferredValue, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { accountApi } from '@/lib/account-api';
import { accountLabel } from '@/lib/auth';
import type { Conversation } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import { queryKeys } from '@/lib/query-keys';
import type { Run } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';
import { useWorkspaceSelection } from '@/lib/workspace-selection';

export function useSidebarModel() {
  const [searchParams] = useSearchParams();
  const selectedConversation = searchParams.get('conversation');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const {
    workspaces: workspaceList,
    activeWorkspace,
    activeWorkspaceId,
    isLoading: workspacesLoading,
    error: workspacesError,
  } = useWorkspaceSelection();
  const workspaces = workspaceList.filter(Boolean);
  const workspaceIds = new Set(workspaces.map((workspace) => workspace.id));

  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [showArchived, setShowArchived] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{
    id: string;
    title: string;
  } | null>(null);

  // 取租户内全部会话(不传 workspace_id),客户端再按当前工作区过滤;
  // 搜索与归档视图仍交给服务端分页。
  const conversationsQuery = useInfiniteQuery({
    queryKey: [...queryKeys.conversations, 'all', showArchived, deferredQuery],
    queryFn: ({ pageParam }) =>
      conversationsApi.page({
        limit: 20,
        q: deferredQuery,
        archived: showArchived,
        cursor: pageParam as string | undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const runsQuery = useQuery({
    queryKey: queryKeys.runs,
    queryFn: runsApi.list,
    refetchInterval: 5000,
  });
  const accountQuery = useQuery({
    queryKey: queryKeys.authAccount,
    queryFn: accountLabel,
  });
  const organizationQuery = useQuery({
    queryKey: queryKeys.organization,
    queryFn: accountApi.organization,
    retry: false,
  });

  const conversations =
    conversationsQuery.data?.pages.flatMap((page) => page.items ?? []) ?? [];
  const searching = deferredQuery.trim().length > 0;

  // 会话区只展示当前工作区的会话;归属为空或指向已删工作区的收进「未分组」。
  const activeSessions = conversations.filter(
    (conversation) => conversation.workspace_id === activeWorkspaceId,
  );
  const ungroupedSessions = conversations.filter(
    (conversation) =>
      !conversation.workspace_id ||
      !workspaceIds.has(conversation.workspace_id),
  );

  // 切换器菜单里的会话数:按已加载的会话统计(与菜单同源,不额外发请求)。
  const sessionCountByWorkspace = new Map<string, number>();
  for (const conversation of conversations) {
    const workspaceId = conversation.workspace_id;
    if (workspaceId) {
      sessionCountByWorkspace.set(
        workspaceId,
        (sessionCountByWorkspace.get(workspaceId) ?? 0) + 1,
      );
    }
  }
  const workspaceCounts = Object.fromEntries(
    workspaces.map((workspace) => [
      workspace.id,
      sessionCountByWorkspace.get(workspace.id) ?? 0,
    ]),
  );

  const invalidateConversations = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.conversationPrefix,
    });
  };

  /** 控制台内所有会话链接都带上工作区:刷新/分享都能还原同一视图。 */
  const workspaceHref = (
    workspaceId: string | null,
    options: { conversation?: string; new?: boolean } = {},
  ) => {
    const search = new URLSearchParams();
    if (workspaceId) search.set('workspace', workspaceId);
    if (options.conversation) search.set('conversation', options.conversation);
    if (options.new) search.set('new', '1');
    const serialized = search.toString();
    return serialized ? '/?' + serialized : '/';
  };

  const newSessionHref = activeWorkspaceId
    ? workspaceHref(activeWorkspaceId, { new: true })
    : '/workspace?new=1';

  /**
   * 会话链接归属的工作区:会话自身的归属优先;
   * 归属为空或已不在工作区清单里(工作区被删)时回落到当前工作区,避免死链。
   */
  const sessionWorkspaceId = (conversation: Conversation): string | null => {
    const workspaceId = conversation.workspace_id;
    if (workspaceId && workspaceIds.has(workspaceId)) {
      return workspaceId;
    }
    return activeWorkspaceId;
  };

  const renameMutation = useMutation({
    mutationFn: (input: { id: string; title: string }) =>
      conversationsApi.update(input.id, { title: input.title }),
    onSuccess: () => {
      setRenaming(null);
      invalidateConversations();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (input: { id: string; archived: boolean }) =>
      conversationsApi.update(input.id, { archived: input.archived }),
    onSuccess: (_data, input) => {
      invalidateConversations();
      if (input.archived && input.id === selectedConversation) {
        navigate(workspaceHref(activeWorkspaceId, { new: true }), {
          replace: true,
        });
      }
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => conversationsApi.remove(id),
    onSuccess: (_data, id) => {
      invalidateConversations();
      if (id === selectedConversation) {
        navigate(workspaceHref(activeWorkspaceId, { new: true }), {
          replace: true,
        });
      }
    },
  });

  const latestRunByConversation = new Map<string, Run>();
  for (const run of runsQuery.data ?? []) {
    if (!run.conversation_id) continue;
    const previous = latestRunByConversation.get(run.conversation_id);
    if (
      !previous ||
      Date.parse(run.created_at) > Date.parse(previous.created_at)
    ) {
      latestRunByConversation.set(run.conversation_id, run);
    }
  }

  const account = accountQuery.data ?? null;
  const plan = organizationQuery.data?.plan;
  const planLabel = plan ? plan + ' 版' : '个人工作区';

  const confirmDelete = (conversation: Conversation) => {
    setMenuFor(null);
    const ok =
      typeof window.confirm !== 'function' ||
      window.confirm('删除会话「' + conversation.title + '」?该操作不可撤销。');
    if (ok) removeMutation.mutate(conversation.id);
  };

  const listLoading = workspacesLoading || conversationsQuery.isPending;
  const listFailed = Boolean(workspacesError) || conversationsQuery.isError;

  const toggleArchived = () => {
    setShowArchived((value) => !value);
    setMenuFor(null);
  };

  return {
    // 查询状态
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    conversations,
    activeSessions,
    ungroupedSessions,
    workspaceCounts,
    latestRunByConversation,
    conversationsQuery,
    account,
    planLabel,
    listLoading,
    listFailed,
    // 视图状态
    query,
    setQuery,
    searching,
    showArchived,
    toggleArchived,
    menuFor,
    setMenuFor,
    renaming,
    setRenaming,
    selectedConversation,
    // 链接
    workspaceHref,
    newSessionHref,
    sessionWorkspaceId,
    // 操作
    renameMutation,
    archiveMutation,
    confirmDelete,
    invalidateConversations,
  };
}

export type SidebarModel = ReturnType<typeof useSidebarModel>;
