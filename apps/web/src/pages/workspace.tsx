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
  Empty,
  Field,
  Input,
  Spin,
} from '@beauty/design';

import { ArtifactPreview } from '@/components/console/artifact-preview';
import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/components/runs/auth-bar';
import {
  artifactFileName,
  isImageArtifact,
  saveBlob,
} from '@/lib/artifacts';
import type { Artifact } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import { isUnauthorized } from '@/lib/http';
import { useBlobUrl } from '@/lib/use-blob-url';
import { cn } from '@/lib/utils';

const WORKSPACES_KEY = ['workspaces'];

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN');
}

/** 工作区控制台:工作区清单 + 交付物(元数据/正文/删除),§7 artifacts 归属 workspace。 */
export function WorkspacePage() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(
    null,
  );
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(
    null,
  );
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => {
      const [workspaces, fallback] = await Promise.all([
        workspaceApi.workspaces(),
        workspaceApi.defaultWorkspace(),
      ]);
      // 默认工作区一定在列表里(服务端幂等建档);列表为空时用它兜底
      return workspaces.length > 0 ? workspaces : [fallback];
    },
  });

  const workspaces = workspacesQuery.data ?? [];
  const activeWorkspaceId = selectedWorkspaceId ?? workspaces[0]?.id ?? null;

  const artifactsQuery = useQuery({
    queryKey: ['workspace-artifacts', activeWorkspaceId],
    queryFn: () => workspaceApi.artifacts(activeWorkspaceId as string),
    enabled: Boolean(activeWorkspaceId),
  });

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
      setSelectedWorkspaceId(workspace.id);
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
    workspacesQuery.error,
    artifactsQuery.error,
    createMutation.error,
    openArtifactMutation.error,
    removeMutation.error,
    downloadMutation.error,
    imageQuery.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canCreate = name.trim().length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="工作区"
        subtitle="交付物元数据在本服务、正文在对象存储;每个 run 的成品都可溯源"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void refreshWorkspaces()} />

        {unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再查看工作区。
          </Alert>
        ) : null}

        <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_2fr]">
          <Card>
            <CardHeader>
              <CardTitle>工作区</CardTitle>
              <CardDescription>共 {workspaces.length} 个</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {workspacesQuery.isLoading ? (
                <Spin label="加载中…" />
              ) : (
                <ul className="space-y-2">
                  {workspaces.map((workspace) => (
                    <li key={workspace.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedWorkspaceId(workspace.id);
                          setSelectedArtifact(null);
                        }}
                        className={cn(
                          'w-full rounded-md border px-3 py-2 text-left text-sm transition-colors',
                          workspace.id === activeWorkspaceId
                            ? 'border-primary bg-muted'
                            : 'hover:bg-muted/60',
                        )}
                      >
                        <span className="font-medium">{workspace.name}</span>
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {workspace.description ?? '无描述'} ·{' '}
                          {formatTime(workspace.created_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <div className="space-y-3 border-t pt-4">
                <Field label="新建工作区" htmlFor="workspace-name">
                  <Input
                    id="workspace-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="销售资料"
                  />
                </Field>
                <Field label="描述(可选)" htmlFor="workspace-description">
                  <Input
                    id="workspace-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </Field>
                <div className="flex items-center gap-3">
                  <Button
                    onClick={() => createMutation.mutate()}
                    disabled={!canCreate || createMutation.isPending}
                  >
                    {createMutation.isPending ? '创建中…' : '创建'}
                  </Button>
                  {createMutation.isError ? (
                    <span className="text-sm text-destructive">
                      {(createMutation.error as Error).message}
                    </span>
                  ) : null}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>交付物</CardTitle>
                <CardDescription>
                  当前工作区共 {artifacts.length} 件;点「查看」打开正文或预览文件。
                </CardDescription>
              </CardHeader>
              <CardContent>
                {artifactsQuery.isLoading ? (
                  <Spin label="加载中…" />
                ) : artifacts.length === 0 ? (
                  <Empty description="这个工作区还没有交付物:先在上面建个 run 并批准执行。" />
                ) : (
                  <ul className="divide-y">
                    {artifacts.map((artifact) => (
                      <li
                        key={artifact.id}
                        className="flex flex-wrap items-center justify-between gap-3 py-3"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-medium">
                            {artifact.filename ?? artifact.title}
                          </p>
                          <p className="mt-1 truncate text-xs text-muted-foreground">
                            {artifact.kind}
                            {artifact.content_type
                              ? ` · ${artifact.content_type}`
                              : null}{' '}
                            · {artifact.storage} ·{' '}
                            {formatSize(artifact.size_bytes)} ·{' '}
                            {artifact.run_id
                              ? `run ${artifact.run_id.slice(0, 8)}…`
                              : '无 run'}{' '}
                            · {formatTime(artifact.created_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              openArtifactMutation.mutate(artifact.id)
                            }
                            disabled={openArtifactMutation.isPending}
                          >
                            查看
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => removeMutation.mutate(artifact.id)}
                            disabled={removeMutation.isPending}
                          >
                            删除
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {removeMutation.isError ? (
                  <p className="mt-3 text-sm text-destructive">
                    {(removeMutation.error as Error).message}
                  </p>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>产物预览</CardTitle>
                <CardDescription>
                  {selectedArtifact
                    ? `${artifactFileName(selectedArtifact)} · ${formatSize(selectedArtifact.size_bytes)}`
                    : '还没选中交付物'}
                </CardDescription>
                {selectedArtifact ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => downloadMutation.mutate(selectedArtifact)}
                    disabled={downloadMutation.isPending}
                  >
                    {downloadMutation.isPending ? '下载中…' : '下载'}
                  </Button>
                ) : null}
              </CardHeader>
              <CardContent>
                {openArtifactMutation.isPending ? (
                  <p className="text-sm text-muted-foreground">读取中…</p>
                ) : selectedArtifact ? (
                  selectedIsImage ? (
                    imageUrl ? (
                      <ArtifactPreview
                        artifact={selectedArtifact}
                        src={imageUrl}
                        className="max-h-96 bg-muted/40"
                      />
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {imageQuery.isPending
                          ? '读取中…'
                          : (imageQuery.error?.message ?? '图片读取失败。')}
                      </p>
                    )
                  ) : (
                    <ArtifactPreview
                      artifact={selectedArtifact}
                      className="max-h-96 bg-muted/40"
                    />
                  )
                ) : (
                  <p className="text-sm text-muted-foreground">
                    在列表里点「查看」,正文或图片会显示在这里。
                  </p>
                )}
                {openArtifactMutation.isError ? (
                  <p className="mt-3 text-sm text-destructive">
                    {(openArtifactMutation.error as Error).message}
                  </p>
                ) : null}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
