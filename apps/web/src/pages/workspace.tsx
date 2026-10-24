import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Download,
  Eye,
  FolderKanban,
  MessagesSquare,
  Package,
  Plus,
  Trash2,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';

import { Field, Input } from '@beauty/design';

import { ArtifactPreview } from '@/components/console/artifact-preview';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  PageHeader,
} from '@/components/console/ui';
import { WorkspaceActions } from '@/components/console/workspace-actions';
import { AuthBar } from '@/components/runs/auth-bar';
import { artifactFileName, isImageArtifact, saveBlob } from '@/lib/artifacts';
import { conversationsApi } from '@/lib/conversations-api';
import { isUnauthorized } from '@/lib/http';
import { formatDateTime, formatSize } from '@/lib/presentation';
import { useBlobUrl } from '@/lib/use-blob-url';
import { cn } from '@/lib/utils';
import type { Artifact } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import {
  WORKSPACES_KEY,
  useWorkspaceSelection,
  type WorkspaceList,
} from '@/lib/workspace-selection';

/** 工作区中心的区块标题:图标 + 文案 + 右侧计数。 */
function SectionHeading({
  icon,
  title,
  hint,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-ha-border px-4 py-2.5">
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-ha-text-2">
        <span className="[&>svg]:h-3.5 [&>svg]:w-3.5 [&>svg]:text-ha-dim">
          {icon}
        </span>
        {title}
      </p>
      <span className="font-mono text-[11px] text-ha-dim">{hint}</span>
    </div>
  );
}

/**
 * 工作区中心(/workspace):左侧工作区清单 + 内联新建,右侧当前工作区的会话、交付物与预览。
 *
 * 工作区即数据层的 workspace:?workspace=<id> 是唯一选中真源(与侧栏/会话页共用),
 * 会话与交付物都按工作区归属;页面不再用亮色卡片,统一走 --ha-* 控制台语言。
 */
export function WorkspacePage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  // 当前工作区来自 URL(?workspace=…),缺省回落到租户默认工作区。
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    isLoading: workspacesLoading,
    error: workspacesError,
    selectWorkspace,
  } = useWorkspaceSelection();
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(
    null,
  );
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  // /workspace?new=1 打开工作区页时直接展开新建表单
  const [formOpen, setFormOpen] = useState(searchParams.get('new') === '1');

  useEffect(() => {
    if (!formOpen) return;
    // Input 不转发 ref:表单挂载/展开后按 id 取焦点,方便直接键入工作区名。
    // 依赖加载态:?new=1 首次进入时表单要等工作区清单解析完才挂载。
    document.getElementById('workspace-name')?.focus();
  }, [formOpen, workspacesLoading]);

  const artifactsQuery = useQuery({
    queryKey: ['workspace-artifacts', activeWorkspaceId],
    queryFn: () => workspaceApi.artifacts(activeWorkspaceId as string),
    enabled: Boolean(activeWorkspaceId),
  });

  // 工作区页展示"这个工作区下的会话":归属过滤由服务端按 workspace_id 完成。
  const conversationsQuery = useQuery({
    queryKey: ['conversations', 'workspace', activeWorkspaceId],
    queryFn: () =>
      conversationsApi.list({
        workspace_id: activeWorkspaceId ?? undefined,
        limit: 20,
      }),
    enabled: Boolean(activeWorkspaceId),
  });
  const workspaceConversations = conversationsQuery.data ?? [];

  const refreshWorkspaces = () =>
    queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
  const refreshArtifacts = () =>
    queryClient.invalidateQueries({
      queryKey: ['workspace-artifacts', activeWorkspaceId],
    });

  const createMutation = useMutation({
    mutationFn: () =>
      workspaceApi.createWorkspace({
        name: name.trim(),
        description: description.trim() || null,
      }),
    onSuccess: async (workspace) => {
      setName('');
      setDescription('');
      setFormOpen(false);
      // 新工作区尚未进列表缓存:先补进去,再切 URL(切完 artifacts 立即按新 id 查询)
      queryClient.setQueryData<WorkspaceList>(WORKSPACES_KEY, (previous) => {
        if (!previous) return previous;
        if (previous.workspaces.some((item) => item.id === workspace.id)) {
          return previous;
        }
        return { ...previous, workspaces: [...previous.workspaces, workspace] };
      });
      selectWorkspace(workspace.id);
      await refreshWorkspaces();
    },
  });

  const openArtifactMutation = useMutation({
    mutationFn: (artifactId: string) => workspaceApi.artifact(artifactId),
    onSuccess: (artifact) => setSelectedArtifact(artifact),
  });

  const downloadMutation = useMutation({
    mutationFn: async (artifact: Artifact) => {
      const blob = await workspaceApi.downloadArtifact(artifact.id);
      saveBlob(blob, artifactFileName(artifact));
    },
  });

  const removeMutation = useMutation({
    mutationFn: (artifactId: string) => workspaceApi.removeArtifact(artifactId),
    onSuccess: async (_, artifactId) => {
      setSelectedArtifact((current) =>
        current?.id === artifactId ? null : current,
      );
      await refreshArtifacts();
    },
  });

  const artifacts = artifactsQuery.data ?? [];
  const selectedIsImage = selectedArtifact
    ? isImageArtifact(selectedArtifact)
    : false;
  const imageQuery = useQuery({
    queryKey: ['workspace-artifact-blob', selectedArtifact?.id],
    queryFn: () =>
      workspaceApi.downloadArtifact(selectedArtifact?.id as string),
    enabled: Boolean(selectedArtifact?.id) && selectedIsImage,
    retry: false,
  });
  const imageUrl = useBlobUrl(imageQuery.data ?? null);
  const errors = [
    workspacesError,
    conversationsQuery.error,
    artifactsQuery.error,
    createMutation.error,
    openArtifactMutation.error,
    removeMutation.error,
    downloadMutation.error,
    imageQuery.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canCreate = name.trim().length > 0;
  const hasNoWorkspaces = !workspacesLoading && workspaces.length === 0;

  const openCreateForm = () => {
    setFormOpen(true);
    document.getElementById('workspace-name')?.focus();
  };

  const createForm = (
    <form
      className="w-full space-y-3 text-left"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canCreate || createMutation.isPending) return;
        createMutation.mutate();
      }}
    >
      <Field label="工作区名称" htmlFor="workspace-name" required>
        <Input
          id="workspace-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="例如:销售资料"
        />
      </Field>
      <Field label="工作区描述(可选)" htmlFor="workspace-description">
        <Input
          id="workspace-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="这个工作区收哪些会话和交付物"
        />
      </Field>
      <div className="flex items-center gap-2">
        <ConsoleButton
          type="submit"
          variant="primary"
          size="sm"
          disabled={!canCreate || createMutation.isPending}
        >
          {createMutation.isPending ? '创建中…' : '创建工作区'}
        </ConsoleButton>
        {formOpen ? (
          <ConsoleButton
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setFormOpen(false)}
          >
            收起
          </ConsoleButton>
        ) : null}
      </div>
      {createMutation.isError ? (
        <p className="text-[12px] text-ha-danger">
          {(createMutation.error as Error).message}
        </p>
      ) : null}
    </form>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="工作区"
        subtitle="每个工作区持有自己的会话与交付物,会话必须归属某个工作区"
        actions={
          <ConsoleButton variant="primary" size="sm" onClick={openCreateForm}>
            <Plus className="h-3.5 w-3.5" />
            新建工作区
          </ConsoleButton>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void refreshWorkspaces()} />

        {unauthorized ? (
          <p className="mt-4 rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning">
            未登录或令牌失效:先在上方面板完成鉴权,再查看工作区。
          </p>
        ) : null}

        {workspacesLoading ? (
          <p className="mt-10 text-center text-[13px] text-ha-muted">加载中…</p>
        ) : hasNoWorkspaces ? (
          <div className="mt-6">
            <EmptyPanel
              icon={<FolderKanban className="h-7 w-7" />}
              title="还没有工作区"
              description="工作区用来收拢会话与交付物;先创建一个,再开始第一段会话。"
              action={
                formOpen ? (
                  <div className="w-full max-w-sm pt-1">{createForm}</div>
                ) : (
                  <ConsoleButton variant="primary" onClick={openCreateForm}>
                    创建第一个工作区
                  </ConsoleButton>
                )
              }
            />
          </div>
        ) : (
          <div className="mt-6 grid gap-6 lg:grid-cols-[260px_1fr]">
            {/*
              左列:工作区清单 + 内联新建表单。
              这里不能加 overflow-hidden:行尾「⋯」菜单是绝对定位,面板会把它裁掉。
            */}
            <ConsolePanel className="flex h-fit flex-col lg:sticky lg:top-0">
              <SectionHeading
                icon={<FolderKanban />}
                title="工作区"
                hint={`共 ${workspaces.length} 个`}
              />
              <ul className="divide-y divide-ha-border">
                {workspaces.map((workspace) => {
                  const selected = workspace.id === activeWorkspaceId;
                  return (
                    <li
                      key={workspace.id}
                      className="group/workspace flex items-center"
                    >
                      {/* 选中区与操作入口是并列的两个按钮,避免 button 套 button */}
                      <button
                        type="button"
                        aria-current={selected ? 'true' : undefined}
                        onClick={() => {
                          selectWorkspace(workspace.id);
                          setSelectedArtifact(null);
                        }}
                        className={cn(
                          'min-w-0 flex-1 border-l-2 px-3 py-2.5 text-left transition-colors',
                          selected
                            ? 'border-ha-accent-soft/60 bg-ha-accent-soft/20'
                            : 'border-transparent hover:bg-ha-panel-2/60',
                        )}
                      >
                        <span className="block truncate text-[13px] font-medium text-ha-text">
                          {workspace.name}
                        </span>
                        <span className="mt-1 block truncate text-[11px] text-ha-muted">
                          {workspace.description?.split('\n')[0] ?? '无描述'}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[11px] text-ha-dim">
                          创建于 {formatDateTime(workspace.created_at)}
                        </span>
                      </button>
                      <WorkspaceActions
                        workspace={workspace}
                        className="mr-2"
                        triggerClassName="opacity-0 transition-opacity group-hover/workspace:opacity-100 group-focus-within/workspace:opacity-100 focus-visible:opacity-100"
                      />
                    </li>
                  );
                })}
              </ul>

              {formOpen ? (
                <div className="border-t border-ha-border p-3">
                  <p className="mb-2 text-[12px] font-medium text-ha-text-2">
                    新建工作区
                  </p>
                  {createForm}
                </div>
              ) : null}
            </ConsolePanel>

            {/* 右列:当前工作区的头部 + 会话 + 交付物 + 预览 */}
            <div className="min-w-0 space-y-6">
              <ConsolePanel className="p-4">
                {activeWorkspace ? (
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-[15px] font-semibold text-ha-text">
                        {activeWorkspace.name}
                      </h2>
                      <p className="mt-1 text-[12px] leading-5 text-ha-muted">
                        {activeWorkspace.description ?? '无描述'}
                      </p>
                      <p className="mt-1 font-mono text-[11px] text-ha-dim">
                        创建于 {formatDateTime(activeWorkspace.created_at)} · 会话{' '}
                        {workspaceConversations.length} 个 · 交付物{' '}
                        {artifacts.length} 个
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {activeWorkspaceId ? (
                        <Link
                          to={'/?workspace=' + activeWorkspaceId + '&new=1'}
                          className={consoleButtonClass({
                            variant: 'primary',
                            size: 'sm',
                          })}
                        >
                          新建会话
                        </Link>
                      ) : null}
                      <a
                        href="#workspace-artifacts"
                        className={consoleButtonClass({
                          variant: 'secondary',
                          size: 'sm',
                        })}
                      >
                        查看交付物
                      </a>
                    </div>
                  </div>
                ) : (
                  <p className="text-[13px] text-ha-muted">
                    先在左侧选择或创建一个工作区。
                  </p>
                )}
              </ConsolePanel>

              <ConsolePanel className="overflow-hidden">
                <SectionHeading
                  icon={<MessagesSquare />}
                  title="会话"
                  hint={`共 ${workspaceConversations.length} 个`}
                />
                {conversationsQuery.isLoading ? (
                  <p className="px-4 py-4 text-[13px] text-ha-muted">加载中…</p>
                ) : workspaceConversations.length === 0 ? (
                  <EmptyPanel
                    className="m-3"
                    title={`在「${activeWorkspace?.name ?? '工作区'}」里开始第一段会话`}
                    description="会话会记录在这里,随时可以从列表继续。"
                    action={
                      activeWorkspaceId ? (
                        <Link
                          to={'/?workspace=' + activeWorkspaceId + '&new=1'}
                          className={consoleButtonClass({
                            variant: 'primary',
                            size: 'sm',
                          })}
                        >
                          新建会话
                        </Link>
                      ) : null
                    }
                  />
                ) : (
                  <ul className="divide-y divide-ha-border">
                    {workspaceConversations.map((conversation) => (
                      <li key={conversation.id}>
                        <Link
                          to={
                            '/?workspace=' +
                            (conversation.workspace_id ?? activeWorkspaceId) +
                            '&conversation=' +
                            conversation.id
                          }
                          className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-ha-panel-2/60"
                        >
                          <span className="truncate text-[13px] text-ha-text-body">
                            {conversation.title}
                          </span>
                          <span className="shrink-0 font-mono text-[11px] text-ha-dim">
                            {formatDateTime(conversation.updated_at)}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </ConsolePanel>

              <ConsolePanel
                id="workspace-artifacts"
                className="overflow-hidden scroll-mt-6"
              >
                <SectionHeading
                  icon={<Package />}
                  title="交付物"
                  hint={`共 ${artifacts.length} 件`}
                />
                {artifactsQuery.isLoading ? (
                  <p className="px-4 py-4 text-[13px] text-ha-muted">加载中…</p>
                ) : artifacts.length === 0 ? (
                  <EmptyPanel
                    className="m-3"
                    title="这个工作区还没有交付物"
                    description="先建一个任务并批准执行,成品会落到这里。"
                  />
                ) : (
                  <ul className="divide-y divide-ha-border">
                    {artifacts.map((artifact) => (
                      <li
                        key={artifact.id}
                        className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[13px] font-medium text-ha-text">
                            {artifact.filename ?? artifact.title}
                          </p>
                          <p className="mt-1 truncate font-mono text-[11px] text-ha-dim">
                            {artifact.kind}
                            {artifact.content_type
                              ? ` · ${artifact.content_type}`
                              : null}{' '}
                            · {artifact.storage} ·{' '}
                            {formatSize(artifact.size_bytes)} ·{' '}
                            {artifact.run_id
                              ? `run ${artifact.run_id.slice(0, 8)}…`
                              : '无 run'}{' '}
                            · {formatDateTime(artifact.created_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <ConsoleButton
                            variant="secondary"
                            size="sm"
                            onClick={() =>
                              openArtifactMutation.mutate(artifact.id)
                            }
                            disabled={openArtifactMutation.isPending}
                          >
                            <Eye className="h-3.5 w-3.5" />
                            查看
                          </ConsoleButton>
                          <ConsoleButton
                            variant="danger"
                            size="sm"
                            onClick={() => removeMutation.mutate(artifact.id)}
                            disabled={removeMutation.isPending}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            删除
                          </ConsoleButton>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {removeMutation.isError ? (
                  <p className="px-4 pb-3 text-[12px] text-ha-danger">
                    {(removeMutation.error as Error).message}
                  </p>
                ) : null}
              </ConsolePanel>

              <ConsolePanel className="overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ha-border px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium text-ha-text-2">
                      产物预览
                    </p>
                    <p className="mt-0.5 truncate font-mono text-[11px] text-ha-dim">
                      {selectedArtifact
                        ? `${artifactFileName(selectedArtifact)} · ${formatSize(selectedArtifact.size_bytes)}`
                        : '还没选中交付物'}
                    </p>
                  </div>
                  {selectedArtifact ? (
                    <ConsoleButton
                      variant="secondary"
                      size="sm"
                      onClick={() => downloadMutation.mutate(selectedArtifact)}
                      disabled={downloadMutation.isPending}
                    >
                      <Download className="h-3.5 w-3.5" />
                      {downloadMutation.isPending ? '下载中…' : '下载'}
                    </ConsoleButton>
                  ) : null}
                </div>
                <div className="p-4">
                  {openArtifactMutation.isPending ? (
                    <p className="text-[13px] text-ha-muted">读取中…</p>
                  ) : selectedArtifact ? (
                    selectedIsImage ? (
                      imageUrl ? (
                        <ArtifactPreview
                          artifact={selectedArtifact}
                          src={imageUrl}
                          className="max-h-96 bg-ha-code"
                        />
                      ) : (
                        <p className="text-[13px] text-ha-muted">
                          {imageQuery.isPending
                            ? '读取中…'
                            : (imageQuery.error?.message ?? '图片读取失败。')}
                        </p>
                      )
                    ) : (
                      <ArtifactPreview
                        artifact={selectedArtifact}
                        className="max-h-96 bg-ha-code"
                      />
                    )
                  ) : (
                    <p className="text-[13px] text-ha-muted">
                      在交付物列表里点「查看」,正文或图片会显示在这里。
                    </p>
                  )}
                  {openArtifactMutation.isError ? (
                    <p className="mt-3 text-[12px] text-ha-danger">
                      {(openArtifactMutation.error as Error).message}
                    </p>
                  ) : null}
                </div>
              </ConsolePanel>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
