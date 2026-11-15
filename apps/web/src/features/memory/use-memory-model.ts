/**
 * 记忆页数据与交互模型:统计/列表/语义检索查询,写入/编辑/置顶/删除/清理,
 * 以及过滤、检索、写入表单、行内编辑与二次确认的局部状态。页面只负责布局与组合。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { isUnauthorized } from '@/lib/http';
import {
  memoryApi,
  type Memory,
  type MemoryImportItem,
  type MemoryInjectionMode,
  type MemoryKind,
  type MemoryScope,
  type MemorySource,
  type MemoryUpdateInput,
} from '@/lib/memory-api';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceList } from '@/lib/workspace-selection';

/** 列表一次取多少条。 */
const LIST_LIMIT = 50;
/** 语义检索一次最多命中多少条。 */
const SEARCH_LIMIT = 20;
/** 有效期上限(天),与后端校验一致。 */
const TTL_MAX_DAYS = 3650;

/** 把导出结果存成 JSON 文件:无需后端额外端点,浏览器端即可完成。 */
function downloadExport(payload: unknown, exportedAt: string) {
  if (typeof URL.createObjectURL !== 'function') return;
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `memories-${exportedAt.slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function useMemoryModel() {
  const queryClient = useQueryClient();

  /* ── 过滤 / 检索状态 ───────────────────────────────────────────────── */
  const [kindFilter, setKindFilter] = useState<MemoryKind | ''>('');
  const [workspaceFilter, setWorkspaceFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState<MemorySource | ''>('');
  const [includeInvalid, setIncludeInvalid] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  /* ── 行内编辑状态 ─────────────────────────────────────────────────── */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState('');

  /* ── 彻底删除二次确认 ─────────────────────────────────────────────── */
  const [confirmingClearAll, setConfirmingClearAll] = useState(false);

  /* ── 写入表单状态 ─────────────────────────────────────────────────── */
  const [content, setContent] = useState('');
  const [newKind, setNewKind] = useState<MemoryKind>('note');
  const [newScope, setNewScope] = useState<MemoryScope>('account');
  const [newWorkspaceId, setNewWorkspaceId] = useState('');
  const [pinned, setPinned] = useState(false);
  const [ttlDays, setTtlDays] = useState('');
  const [injectionMode, setInjectionMode] =
    useState<MemoryInjectionMode>('retrieved');

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
      sourceFilter || undefined,
      includeInvalid,
    ),
    queryFn: () =>
      memoryApi.list({
        kind: kindFilter || undefined,
        workspaceId: workspaceFilter || undefined,
        sourceType: sourceFilter || undefined,
        includeInvalid,
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
        injection_mode: injectionMode,
      }),
    onSuccess: async () => {
      setContent('');
      setPinned(false);
      setTtlDays('');
      setInjectionMode('retrieved');
      setNotice('已写入记忆');
      await refresh();
    },
  });

  const updateMutation = useMutation({
    mutationFn: (variables: { id: string; input: MemoryUpdateInput }) =>
      memoryApi.update(variables.id, variables.input),
    onSuccess: async () => {
      setEditingId(null);
      setEditingContent('');
      setNotice('已更新记忆');
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

  const clearAllMutation = useMutation({
    mutationFn: () => memoryApi.removeAll(),
    onSuccess: async (result) => {
      setConfirmingClearAll(false);
      setNotice(
        '已彻底删除 ' +
          result.memories +
          ' 条记忆与 ' +
          result.reviews +
          ' 条复核项',
      );
      await refresh();
    },
  });

  const importMutation = useMutation({
    mutationFn: (items: MemoryImportItem[]) => memoryApi.importAll(items),
    onSuccess: async (result) => {
      setNotice(
        '已导入 ' +
          result.created +
          ' 条记忆(跳过重复 ' +
          result.skipped_duplicate +
          ' 条,非法 ' +
          result.skipped_invalid +
          ' 条)',
      );
      await refresh();
    },
  });

  const reembedMutation = useMutation({
    mutationFn: () => memoryApi.reembed(),
    onSuccess: async (result) => {
      setNotice(
        '已重建 ' +
          result.updated +
          ' 条向量(扫描 ' +
          result.scanned +
          ' 条,模型 ' +
          result.model +
          ')',
      );
      await refresh();
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => memoryApi.exportAll(),
    onSuccess: (result) => {
      downloadExport(result, result.exported_at);
      setNotice('已导出 ' + result.memories.length + ' 条记忆');
    },
  });

  /* ── 操作入口 ─────────────────────────────────────────────────────── */
  const startEdit = (memory: Memory) => {
    setEditingId(memory.id);
    setEditingContent(memory.content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingContent('');
  };

  const saveEdit = (memory: Memory) => {
    const next = editingContent.trim();
    if (!next || next === memory.content) {
      cancelEdit();
      return;
    }
    updateMutation.mutate({
      id: memory.id,
      input: { content: next, expected_updated_at: memory.updated_at },
    });
  };

  const togglePinned = (memory: Memory) => {
    updateMutation.mutate({
      id: memory.id,
      input: { pinned: !memory.pinned, expected_updated_at: memory.updated_at },
    });
  };

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
    updateMutation.error,
    removeMutation.error,
    purgeMutation.error,
    clearAllMutation.error,
    exportMutation.error,
    importMutation.error,
    reembedMutation.error,
  ].some(isUnauthorized);
  const mutationError =
    createMutation.error ??
    updateMutation.error ??
    removeMutation.error ??
    purgeMutation.error ??
    clearAllMutation.error ??
    exportMutation.error ??
    importMutation.error ??
    reembedMutation.error;

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
    sourceFilter,
    setSourceFilter,
    includeInvalid,
    setIncludeInvalid,
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
    injectionMode,
    setInjectionMode,
    ttlError,
    canCreate,
    // 行内编辑
    editingId,
    editingContent,
    setEditingContent,
    startEdit,
    cancelEdit,
    saveEdit,
    togglePinned,
    // 删除/彻底删除二次确认与提示
    confirmingId,
    setConfirmingId,
    confirmingClearAll,
    setConfirmingClearAll,
    noticeText,
    // 错误态
    unauthorized,
    queryError,
    mutationError,
    // 操作
    createMutation,
    updateMutation,
    removeMutation,
    purgeMutation,
    clearAllMutation,
    exportMutation,
    importMutation,
    reembedMutation,
    refresh,
  };
}

export type MemoryModel = ReturnType<typeof useMemoryModel>;
