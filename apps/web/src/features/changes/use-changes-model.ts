/**
 * 变更与产物页的数据与交互模型:默认工作区 → 产物清单 → 选中产物正文/图片,
 * 以及 diff 视图模式、复制与下载等操作。页面只负责布局与组合。
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import {
  artifactFileName,
  isImageArtifact,
  isTextArtifact,
  saveBlob,
} from '@/lib/artifacts';
import { diffStats, parseUnifiedDiff } from '@/lib/presentation';
import { isUnauthorized } from '@/lib/runs-api';
import { useBlobUrl } from '@/lib/use-blob-url';
import { workspaceApi } from '@/lib/workspace-api';
import { queryKeys } from '@/lib/query-keys';

export type ViewMode = 'inline' | 'split';

export function useChangesModel() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>('inline');
  const [copied, setCopied] = useState(false);

  const workspaceQuery = useQuery({
    queryKey: queryKeys.defaultWorkspace,
    queryFn: workspaceApi.defaultWorkspace,
    retry: false,
  });
  const workspace = workspaceQuery.data;
  const artifactsQuery = useQuery({
    queryKey: queryKeys.workspaceArtifacts(workspace?.id),
    queryFn: () => workspaceApi.artifacts(workspace?.id as string),
    enabled: Boolean(workspace?.id),
    refetchInterval: 8000,
  });

  const artifacts = useMemo(
    () => artifactsQuery.data ?? [],
    [artifactsQuery.data],
  );
  const selected =
    artifacts.find((item) => item.id === selectedId) ?? artifacts[0] ?? null;

  const isImage = selected ? isImageArtifact(selected) : false;
  const isText = selected ? isTextArtifact(selected) : false;

  const contentQuery = useQuery({
    queryKey: queryKeys.workspaceArtifact(selected?.id),
    queryFn: () => workspaceApi.artifact(selected?.id as string),
    enabled: Boolean(selected?.id) && !isImage,
  });
  const content = contentQuery.data?.content ?? '';
  const imageQuery = useQuery({
    queryKey: queryKeys.workspaceArtifactBlob(selected?.id),
    queryFn: () => workspaceApi.downloadArtifact(selected?.id as string),
    enabled: Boolean(selected?.id) && isImage,
    retry: false,
  });
  const imageUrl = useBlobUrl(imageQuery.data ?? null);
  const isDiff = Boolean(parseUnifiedDiff(content));
  const stats = diffStats(content);
  const totalBytes = artifacts.reduce((sum, item) => sum + item.size_bytes, 0);
  const runCount = new Set(artifacts.map((item) => item.run_id).filter(Boolean))
    .size;

  const error =
    workspaceQuery.error ?? artifactsQuery.error ?? contentQuery.error;
  const unauthorized = isUnauthorized(error);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用时静默:下载仍可用
    }
  };

  const downloadMutation = useMutation({
    mutationFn: async () => {
      if (!selected) return;
      const blob = await workspaceApi.downloadArtifact(selected.id);
      saveBlob(blob, artifactFileName(selected));
    },
  });
  const download = () => downloadMutation.mutate();

  return {
    // 清单与选中
    artifacts,
    artifactsPending: artifactsQuery.isPending,
    selected,
    setSelectedId,
    totalBytes,
    runCount,
    // 正文与预览
    content,
    contentPending: contentQuery.isPending,
    imageQuery,
    imageUrl,
    isImage,
    isText,
    isDiff,
    stats,
    // diff 视图与操作
    view,
    setView,
    copied,
    copy,
    download,
    downloadPending: downloadMutation.isPending,
    // 错误
    error,
    unauthorized,
  };
}

export type ChangesModel = ReturnType<typeof useChangesModel>;
