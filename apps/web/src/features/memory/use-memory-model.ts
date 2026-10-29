/**
 * 记忆页数据与交互模型:统计/列表/语义检索查询,写入/删除/过期清理变更,
 * 以及过滤、检索、写入表单与二次确认的局部状态。页面只负责布局与组合。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { isUnauthorized } from '@/lib/http';
import { memoryApi, type MemoryKind, type MemoryScope } from '@/lib/memory-api';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceList } from '@/lib/workspace-selection';

/** 列表一次取多少条。 */
const LIST_LIMIT = 50;
/** 语义检索一次最多命中多少条。 */
const SEARCH_LIMIT = 20;
/** 有效期上限(天),与后端校验一致。 */
const TTL_MAX_DAYS = 3650;

export function useMemoryModel() {
  const queryClient = useQueryClient();

  /* ── 过滤 / 检索状态 ───────────────────────────────────────────────── */
  const [kindFilter, setKindFilter] = useState<MemoryKind | ''>('');
  const [workspaceFilter, setWorkspaceFilter] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  /* ── 写入表单状态 ─────────────────────────────────────────────────── */
  const [content, setContent] = useState('');
  const [newKind, setNewKind] = useState<MemoryKind>('note');
  const [newScope, setNewScope] = useState<MemoryScope>('tenant');
  const [newWorkspaceId, setNewWorkspaceId] = useState('');
  const [pinned, setPinned] = useState(false);
  const [ttlDays, setTtlDays] = useState('');

  const ttlText = ttlDays.trim();
  const ttlValue = ttlText === '' ? null : Number(ttlText);
  const ttlError =
    ttlValue !== null &&
    (!Number.isInteger(ttlValue) || ttlValue < 0 || ttlValue > TTL_MAX_DAYS)
      ? '有效期需为 0~3650 的整数天'
      : null;
  const canCreate = content.trim().length > 0 && ttlError === null;

  /* ── 查询 ─────────────────────────────────────────────────────────── */
  // 与侧栏共用同一份工作区缓存:缓存里是 WorkspaceList
  // (`{workspaces, defaultWorkspace}`),这里只取清单。
  const workspacesQuery = useWorkspaceList();
  const workspaces = workspacesQuery.data?.workspaces ?? [];
  const workspaceNames = new Map(
    workspaces.map((item) => [item.id, item.name]),
  );

  const statsQuery = useQuery({
    queryKey: queryKeys.memoryStats,
    queryFn: () => memoryApi.stats(),
  });

  const listQuery = useQuery({
    queryKey: queryKeys.memoryList(
      kindFilter || undefined,
      workspaceFilter || undefined,
    ),
    queryFn: () =>
      memoryApi.list({
        kind: kindFilter || undefined,
        workspaceId: workspaceFilter || undefined,
        limit: LIST_LIMIT,
      }),
  });

  const searchQuery = useQuery({
    queryKey: queryKeys.memorySearch(
      submittedQuery,
      kindFilter || undefined,
      workspaceFilter || undefined,
    ),
    queryFn: () =>
      memoryApi.search({
        query: submittedQuery,
        kind: kindFilter || undefined,
        workspaceId: workspaceFilter || undefined,
        limit: SEARCH_LIMIT,
      }),
    enabled: submittedQuery.length > 0,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.memories });
  };

  /* ── 写操作 ───────────────────────────────────────────────────────── */
  const createMutation = useMutation({
    mutationFn: () =>
      memoryApi.create({
        content: content.trim(),
        kind: newKind,
        scope: newScope,
        workspace_id: newScope === 'workspace' ? newWorkspaceId || null : null,
        pinned,
        ttl_days: ttlValue,
      }),
    onSuccess: async () => {
      setContent('');
      setPinned(false);
      setTtlDays('');
      setNotice('已写入记忆');
      await refresh();
    },
  });

  const removeMutation = useMutation({
    mutationFn: (memoryId: string) => memoryApi.remove(memoryId),
    onSuccess: async () => {
      setConfirmingId(null);
      setNotice('已删除记忆');
      await refresh();
    },
  });

  const purgeMutation = useMutation({
    mutationFn: () => memoryApi.purgeExpired(),
    onSuccess: async (result) => {
      setNotice('已清理 ' + result.purged + ' 条过期记忆');
      await refresh();
    },
  });

  /* ── 错误与加载态 ─────────────────────────────────────────────────── */
  const queryError = [
    statsQuery.error,
    listQuery.error,
    searchQuery.error,
  ].find(Boolean);
  const unauthorized = [
    statsQuery.error,
    listQuery.error,
    searchQuery.error,
    createMutation.error,
    removeMutation.error,
    purgeMutation.error,
  ].some(isUnauthorized);
  const mutationError =
    createMutation.error ?? removeMutation.error ?? purgeMutation.error;

  const stats = statsQuery.data;
  const memories = listQuery.data ?? [];
  const hits = searchQuery.data?.hits ?? [];
  const noticeText =
    notice ??
    (purgeMutation.isSuccess
      ? '已清理 ' + (purgeMutation.data?.purged ?? 0) + ' 条过期记忆'
      : null);

  const submitSearch = () => {
    const next = searchInput.trim();
    if (!next) return;
    setSubmittedQuery(next);
  };

  const clearSearch = () => {
    setSubmittedQuery('');
    setSearchInput('');
  };

  return {
    // 过滤 / 检索
    kindFilter,
    setKindFilter,
    workspaceFilter,
    setWorkspaceFilter,
    workspaces,
    workspaceNames,
    searchInput,
    setSearchInput,
    submittedQuery,
    submitSearch,
    clearSearch,
    // 列表与检索结果
    stats,
    memories,
    hits,
    listLoading: listQuery.isLoading,
    searchLoading: searchQuery.isLoading,
    // 写入表单
    content,
    setContent,
    newKind,
    setNewKind,
    newScope,
    setNewScope,
    newWorkspaceId,
    setNewWorkspaceId,
    pinned,
    setPinned,
    ttlDays,
    setTtlDays,
    ttlError,
    canCreate,
    // 删除二次确认与提示
    confirmingId,
    setConfirmingId,
    noticeText,
    // 错误态
    unauthorized,
    queryError,
    mutationError,
    // 操作
    createMutation,
    removeMutation,
    purgeMutation,
    refresh,
  };
}

export type MemoryModel = ReturnType<typeof useMemoryModel>;
