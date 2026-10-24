/**
 * 工作区中心的数据与交互模型:选中工作区来自 URL(?workspace=…),
 * 会话/交付物按工作区归属查询,交付物预览与下载等动作都在这里收拢。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { artifactFileName, isImageArtifact, saveBlob } from '@/lib/artifacts';
import { conversationsApi } from '@/lib/conversations-api';
import { isUnauthorized } from '@/lib/http';
import { useBlobUrl } from '@/lib/use-blob-url';
import type { Artifact } from '@/lib/workspace-api';
import { workspaceApi } from '@/lib/workspace-api';
import {
  WORKSPACES_KEY,
  useWorkspaceSelection,
  type WorkspaceList,
} from '@/lib/workspace-selection';
import { queryKeys } from '@/lib/query-keys';

export function useWorkspacePageModel() {
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
    queryKey: queryKeys.workspaceArtifacts(activeWorkspaceId),
    queryFn: () => workspaceApi.artifacts(activeWorkspaceId as string),
    enabled: Boolean(activeWorkspaceId),
  });

  // 工作区页展示"这个工作区下的会话":归属过滤由服务端按 workspace_id 完成。
  const conversationsQuery = useQuery({
    queryKey: [...queryKeys.conversations, 'workspace', activeWorkspaceId],
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
      queryKey: queryKeys.workspaceArtifacts(activeWorkspaceId),
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
    queryKey: queryKeys.workspaceArtifactBlob(selectedArtifact?.id),
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

  return {
    // 选中与清单
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    workspacesLoading,
    selectWorkspace,
    hasNoWorkspaces,
    // 表单
    name,
    setName,
    description,
    setDescription,
    formOpen,
    setFormOpen,
    canCreate,
    openCreateForm,
    // 查询结果
    artifacts,
    workspaceConversations,
    artifactsLoading: artifactsQuery.isLoading,
    conversationsLoading: conversationsQuery.isLoading,
    // 交付物预览
    selectedArtifact,
    setSelectedArtifact,
    selectedIsImage,
    imageUrl,
    imagePending: imageQuery.isPending,
    imageError: imageQuery.error,
    // 变更
    createMutation,
    openArtifactMutation,
    downloadMutation,
    removeMutation,
    // 刷新与错误
    refreshWorkspaces,
    unauthorized,
  };
}

export type WorkspacePageModel = ReturnType<typeof useWorkspacePageModel>;
