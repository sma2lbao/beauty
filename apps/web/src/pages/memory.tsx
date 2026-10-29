import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Checkbox,
  Empty,
  Field,
  Input,
  Select,
  Spin,
  Tag,
  Textarea,
} from '@beauty/design';

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { isUnauthorized } from '@/lib/http';
import {
  MEMORY_KINDS,
  MEMORY_SCOPES,
  type Memory,
  type MemoryKind,
  type MemoryScope,
  memoryApi,
} from '@/lib/memory-api';
import { formatDateTime } from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceList } from '@/lib/workspace-selection';

/** 记忆类型的中文名。 */
const KIND_LABELS: Record<MemoryKind, string> = {
  note: '笔记',
  preference: '偏好',
  fact: '事实',
  procedure: '流程',
};

/** 类型徽章颜色。 */
const KIND_COLORS: Record<
  MemoryKind,
  'neutral' | 'primary' | 'success' | 'info'
> = {
  note: 'neutral',
  preference: 'primary',
  fact: 'success',
  procedure: 'info',
};

const SCOPE_LABELS: Record<MemoryScope, string> = {
  tenant: '租户级',
  workspace: '工作区级',
};

/** 统计卡定义(后端 GET /memories/stats 的四个字段)。 */
const STAT_ITEMS = [
  { key: 'total', label: '记忆总数', hint: '租户内全部记忆' },
  { key: 'active', label: '有效', hint: '未过期、可被检索' },
  { key: 'pinned', label: '置顶', hint: '始终注入上下文' },
  { key: 'expired', label: '已过期', hint: '可清理' },
] as const;

const LIST_LIMIT = 50;
const SEARCH_LIMIT = 20;

type MemoryRowProps = {
  memory: Memory;
  workspaceName?: string;
  confirming: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
};

/** 单条记忆:徽章 + 内容(截断)+ 元信息 + 删除(二次确认)。 */
function MemoryRow({
  memory,
  workspaceName,
  confirming,
  deleting,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: MemoryRowProps) {
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Tag color={KIND_COLORS[memory.kind]}>
              {KIND_LABELS[memory.kind]}
            </Tag>
            <Tag color={memory.scope === 'workspace' ? 'info' : 'neutral'}>
              {SCOPE_LABELS[memory.scope]}
            </Tag>
            {memory.pinned ? <Tag color="warning">置顶</Tag> : null}
            {memory.expires_at ? (
              <span className="text-xs text-muted-foreground">
                过期于 {formatDateTime(memory.expires_at)}
              </span>
            ) : null}
          </div>
          <p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-sm">
            {memory.content}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            来源 {memory.source_type} · 命中 {memory.access_count} 次 · 更新于{' '}
            {formatDateTime(memory.updated_at)}
            {workspaceName ? ' · 工作区 ' + workspaceName : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {confirming ? (
            <>
              <Button
                variant="danger"
                size="sm"
                disabled={deleting}
                onClick={onConfirmDelete}
              >
                {deleting ? '删除中…' : '确认删除'}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={deleting}
                onClick={onCancelDelete}
              >
                取消
              </Button>
            </>
          ) : (
            <Button variant="outline" size="sm" onClick={onRequestDelete}>
              删除
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

/** 长效记忆管理页:统计、检索、写入、删除与过期清理(§7 记忆)。 */
export function MemoryPage() {
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
    (!Number.isInteger(ttlValue) || ttlValue < 0 || ttlValue > 3650)
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="记忆"
        subtitle="长效记忆:偏好/事实/流程沉淀后可跨会话检索,置顶条目始终注入上下文"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => purgeMutation.mutate()}
            disabled={purgeMutation.isPending || (stats?.expired ?? 0) === 0}
          >
            {purgeMutation.isPending ? '清理中…' : '清理过期'}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void refresh()} />

        {unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再管理记忆。
          </Alert>
        ) : queryError ? (
          <Alert variant="danger" className="mt-4">
            {queryError instanceof Error
              ? queryError.message
              : '记忆数据加载失败'}
          </Alert>
        ) : null}

        {noticeText ? (
          <Alert variant="success" className="mt-4">
            {noticeText}
          </Alert>
        ) : null}

        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STAT_ITEMS.map((item) => (
            <Card key={item.key}>
              <CardHeader>
                <CardDescription>{item.label}</CardDescription>
                <CardTitle className="text-3xl">
                  {stats?.[item.key] ?? '—'}
                </CardTitle>
                <CardDescription>{item.hint}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* 左列:检索 + 列表 */}
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle>记忆列表</CardTitle>
                  <CardDescription>
                    {listQuery.isLoading
                      ? '加载中…'
                      : '共 ' + memories.length + ' 条(最新在前)'}
                  </CardDescription>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label
                    className="text-sm text-muted-foreground"
                    htmlFor="memory-kind-filter"
                  >
                    类型
                  </label>
                  <Select
                    id="memory-kind-filter"
                    className="w-32"
                    value={kindFilter}
                    onChange={(event) =>
                      setKindFilter(event.target.value as MemoryKind | '')
                    }
                  >
                    <option value="">全部</option>
                    {MEMORY_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {KIND_LABELS[kind]}
                      </option>
                    ))}
                  </Select>
                  <label
                    className="text-sm text-muted-foreground"
                    htmlFor="memory-workspace-filter"
                  >
                    工作区
                  </label>
                  <Select
                    id="memory-workspace-filter"
                    className="w-40"
                    value={workspaceFilter}
                    onChange={(event) => setWorkspaceFilter(event.target.value)}
                  >
                    <option value="">全部</option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap items-end gap-2">
                <Field
                  label="检索"
                  htmlFor="memory-search"
                  className="min-w-56 flex-1"
                >
                  <Input
                    id="memory-search"
                    placeholder="按语义检索记忆,例如「回复语言偏好」"
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') submitSearch();
                    }}
                  />
                </Field>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={submitSearch}
                  disabled={!searchInput.trim()}
                >
                  检索
                </Button>
                {submittedQuery ? (
                  <Button variant="outline" size="sm" onClick={clearSearch}>
                    清空检索
                  </Button>
                ) : null}
              </div>

              {submittedQuery ? (
                <div className="mt-4 rounded-md border border-dashed p-3">
                  {searchQuery.isLoading ? (
                    <Spin label="检索中…" />
                  ) : hits.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      「{submittedQuery}」没有命中记忆。
                    </p>
                  ) : (
                    <>
                      <p className="text-sm text-muted-foreground">
                        「{submittedQuery}」命中 {hits.length} 条
                      </p>
                      <ul className="divide-y">
                        {hits.map((memory) => (
                          <MemoryRow
                            key={'search-' + memory.id}
                            memory={memory}
                            workspaceName={
                              memory.workspace_id
                                ? workspaceNames.get(memory.workspace_id)
                                : undefined
                            }
                            confirming={confirmingId === memory.id}
                            deleting={
                              removeMutation.isPending &&
                              removeMutation.variables === memory.id
                            }
                            onRequestDelete={() => setConfirmingId(memory.id)}
                            onConfirmDelete={() =>
                              removeMutation.mutate(memory.id)
                            }
                            onCancelDelete={() => setConfirmingId(null)}
                          />
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              ) : null}

              <div className="mt-4">
                {listQuery.isLoading ? (
                  <Spin label="加载中…" />
                ) : memories.length === 0 ? (
                  <Empty description="还没有记忆:在右侧写入偏好或事实,下次对话会自动带上。" />
                ) : (
                  <ul className="divide-y">
                    {memories.map((memory) => (
                      <MemoryRow
                        key={memory.id}
                        memory={memory}
                        workspaceName={
                          memory.workspace_id
                            ? workspaceNames.get(memory.workspace_id)
                            : undefined
                        }
                        confirming={confirmingId === memory.id}
                        deleting={
                          removeMutation.isPending &&
                          removeMutation.variables === memory.id
                        }
                        onRequestDelete={() => setConfirmingId(memory.id)}
                        onConfirmDelete={() => removeMutation.mutate(memory.id)}
                        onCancelDelete={() => setConfirmingId(null)}
                      />
                    ))}
                  </ul>
                )}
              </div>

              {mutationError ? (
                <p className="mt-3 text-sm text-destructive">
                  {mutationError instanceof Error
                    ? mutationError.message
                    : '操作失败'}
                </p>
              ) : null}
            </CardContent>
          </Card>

          {/* 右列:写入表单 */}
          <Card className="h-fit">
            <CardHeader>
              <CardTitle>写入记忆</CardTitle>
              <CardDescription>
                沉淀稳定的偏好/事实/流程;按语义检索,置顶条目始终注入上下文。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label="记忆内容" htmlFor="memory-content" required>
                <Textarea
                  id="memory-content"
                  rows={4}
                  placeholder="例如:回复默认使用中文,并先给结论。"
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                />
              </Field>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="记忆类型" htmlFor="memory-kind-new">
                  <Select
                    id="memory-kind-new"
                    value={newKind}
                    onChange={(event) =>
                      setNewKind(event.target.value as MemoryKind)
                    }
                  >
                    {MEMORY_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {KIND_LABELS[kind]}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="作用域" htmlFor="memory-scope-new">
                  <Select
                    id="memory-scope-new"
                    value={newScope}
                    onChange={(event) =>
                      setNewScope(event.target.value as MemoryScope)
                    }
                  >
                    {MEMORY_SCOPES.map((scope) => (
                      <option key={scope} value={scope}>
                        {SCOPE_LABELS[scope]}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              {newScope === 'workspace' ? (
                <Field
                  label="关联工作区"
                  htmlFor="memory-workspace-new"
                  hint="可留空:不绑定具体工作区。"
                >
                  <Select
                    id="memory-workspace-new"
                    value={newWorkspaceId}
                    onChange={(event) => setNewWorkspaceId(event.target.value)}
                  >
                    <option value="">不绑定</option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="有效期(天)"
                  htmlFor="memory-ttl"
                  hint="留空表示永久。"
                  error={ttlError}
                >
                  <Input
                    id="memory-ttl"
                    type="number"
                    min={0}
                    max={3650}
                    placeholder="例如 30"
                    value={ttlDays}
                    onChange={(event) => setTtlDays(event.target.value)}
                  />
                </Field>
                <div className="flex items-end pb-2">
                  <Checkbox
                    id="memory-pinned"
                    checked={pinned}
                    onCheckedChange={(checked) => setPinned(checked)}
                    label="置顶(始终注入)"
                  />
                </div>
              </div>

              <Button
                onClick={() => createMutation.mutate()}
                disabled={!canCreate || createMutation.isPending}
              >
                {createMutation.isPending ? '写入中…' : '写入记忆'}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
