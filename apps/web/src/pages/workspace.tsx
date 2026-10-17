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
  Input,
  Spin,
} from '@beauty/design';

import { AuthBar } from '@/components/runs/auth-bar';
import type { Artifact } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import { isUnauthorized } from '@/lib/http';
import { cn } from '@/lib/utils';

const FIELD_CLASS = 'mt-1';
const LABEL_CLASS = 'block text-sm font-medium';

const WORKSPACES_KEY = ['workspaces'];

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

/** 工作区控制台:工作区清单 + 交付物(元数据/正文/删除),§7 artifacts 归属 workspace。 */
export function WorkspacePage() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(
    null,
  );
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
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
  const errors = [
    workspacesQuery.error,
    artifactsQuery.error,
    createMutation.error,
    openArtifactMutation.error,
    removeMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canCreate = name.trim().length > 0;

  return (
    <section className="mx-auto max-w-6xl px-6 py-12">
      <h1 className="font-display text-3xl">工作区</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        工作区存放团队的交付物(§7:元数据在本服务,正文在对象存储)。
        Agent 每完成一个 run,成品会镜像到这里,可用 run 溯源。
      </p>

      <div className="mt-6">
        <AuthBar onChange={() => void refreshWorkspaces()} />
      </div>

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
              <div>
                <label className={LABEL_CLASS} htmlFor="workspace-name">
                  新建工作区
                </label>
                <Input
                  id="workspace-name"
                  className={FIELD_CLASS}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="销售资料"
                />
              </div>
              <div>
                <label className={LABEL_CLASS} htmlFor="workspace-description">
                  描述(可选)
                </label>
                <Input
                  id="workspace-description"
                  className={FIELD_CLASS}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </div>
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
                当前工作区共 {artifacts.length} 件;点「查看」读正文。
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
                      <div>
                        <p className="font-medium">{artifact.title}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {artifact.kind} · {artifact.storage} ·{' '}
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
              <CardTitle>正文预览</CardTitle>
              <CardDescription>
                {selectedArtifact
                  ? `${selectedArtifact.title} · ${formatSize(selectedArtifact.size_bytes)}`
                  : '还没选中交付物'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {openArtifactMutation.isPending ? (
                <p className="text-sm text-muted-foreground">读取中…</p>
              ) : selectedArtifact ? (
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-sm">
                  {selectedArtifact.content}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">
                  在列表里点「查看」,正文会显示在这里。
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
    </section>
  );
}
