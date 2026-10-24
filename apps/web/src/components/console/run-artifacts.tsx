/**
 * run 的交付物列表(报告 + 真实文件):文件名、类型、大小,文本/图片可预览,
 * 其余给下载按钮。下载走带鉴权的二进制端点(fetch → Blob → 本地落盘),
 * 不能用 `<a href>` 直接指向接口。
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { Download, FileText, Eye, Image as ImageIcon } from 'lucide-react';
import { useState } from 'react';

import { ArtifactPreview } from '@/components/console/artifact-preview';
import { ConsolePanel, ToneBadge } from '@/components/console/ui';
import {
  artifactContentType,
  artifactFileName,
  isImageArtifact,
  isTextArtifact,
  saveBlob,
} from '@/lib/artifacts';
import { formatBytes } from '@/lib/presentation';
import { runsApi, type ArtifactMeta } from '@/lib/runs-api';
import { useBlobUrl } from '@/lib/use-blob-url';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/query-keys';

export function RunArtifacts({
  runId,
  className,
}: {
  runId: string;
  className?: string;
}) {
  const [previewId, setPreviewId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: queryKeys.runArtifacts(runId),
    queryFn: () => runsApi.artifacts(runId),
    retry: false,
  });

  const artifacts = listQuery.data ?? [];
  const preview = artifacts.find((item) => item.id === previewId) ?? null;
  const previewable = preview
    ? isTextArtifact(preview) || isImageArtifact(preview)
    : false;
  const previewIsImage = preview ? isImageArtifact(preview) : false;

  const previewQuery = useQuery({
    queryKey: queryKeys.runArtifactPreview(runId, previewId),
    queryFn: async () => {
      const blob = await runsApi.downloadArtifact(runId, previewId as string);
      return { blob, text: previewIsImage ? null : await blob.text() };
    },
    enabled: Boolean(previewId) && previewable,
    retry: false,
  });

  const imageUrl = useBlobUrl(
    previewIsImage ? (previewQuery.data?.blob ?? null) : null,
  );

  return (
    <ConsolePanel className={cn('p-3', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">交付物</span>
        <span className="font-mono text-[11px] text-ha-faint">
          {artifacts.length} 件
        </span>
      </div>

      {listQuery.isLoading ? (
        <p className="mt-2 text-[12px] text-ha-dim">读取中…</p>
      ) : listQuery.isError || artifacts.length === 0 ? (
        <p className="mt-2 text-[12px] text-ha-dim">本任务没有文件产物。</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {artifacts.map((artifact) => (
            <ArtifactRow
              key={artifact.id}
              runId={runId}
              artifact={artifact}
              active={artifact.id === previewId}
              onPreview={() =>
                setPreviewId((current) =>
                  current === artifact.id ? null : artifact.id,
                )
              }
            />
          ))}
        </ul>
      )}

      {preview ? (
        <div className="mt-3 border-t border-ha-border pt-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[11px] text-ha-text-2">
              {artifactFileName(preview)}
            </span>
            <button
              type="button"
              onClick={() => setPreviewId(null)}
              className="text-[11px] text-ha-muted hover:text-ha-text"
            >
              收起预览
            </button>
          </div>
          <div className="mt-2">
            {previewQuery.isLoading ? (
              <p className="text-[12px] text-ha-dim">读取中…</p>
            ) : previewQuery.isError ? (
              <p className="text-[12px] text-ha-danger">
                {previewQuery.error.message}
              </p>
            ) : previewIsImage ? (
              imageUrl ? (
                <ArtifactPreview artifact={preview} src={imageUrl} />
              ) : (
                <p className="text-[12px] text-ha-dim">读取中…</p>
              )
            ) : (
              <ArtifactPreview
                artifact={{ ...preview, content: previewQuery.data?.text }}
              />
            )}
          </div>
        </div>
      ) : null}
    </ConsolePanel>
  );
}

function ArtifactRow({
  runId,
  artifact,
  active,
  onPreview,
}: {
  runId: string;
  artifact: ArtifactMeta;
  active: boolean;
  onPreview: () => void;
}) {
  const fileName = artifactFileName(artifact);
  const isImage = isImageArtifact(artifact);
  const isText = isTextArtifact(artifact);
  const canPreview = isImage || isText;

  const downloadMutation = useMutation({
    mutationFn: async () => {
      const blob = await runsApi.downloadArtifact(runId, artifact.id);
      saveBlob(blob, fileName);
    },
  });

  return (
    <li
      className={cn(
        'rounded-md border px-3 py-2 transition-colors',
        active
          ? 'border-ha-border-strong bg-ha-panel-2'
          : 'border-ha-border bg-ha-panel',
      )}
    >
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded bg-ha-code text-ha-muted">
          {isImage ? (
            <ImageIcon className="h-3.5 w-3.5" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[12px] text-ha-text" title={fileName}>
            {fileName}
          </p>
          <p className="truncate font-mono text-[11px] text-ha-faint">
            {artifactContentType(artifact)}
            {typeof artifact.size_bytes === 'number'
              ? ' · ' + formatBytes(artifact.size_bytes)
              : null}
            {artifact.title && artifact.title !== fileName
              ? ' · ' + artifact.title
              : null}
          </p>
        </div>
        {canPreview ? (
          <button
            type="button"
            aria-label={active ? '收起 ' + fileName : '预览 ' + fileName}
            onClick={onPreview}
            className="grid h-6 w-6 shrink-0 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
          >
            <Eye className="h-3.5 w-3.5" />
          </button>
        ) : null}
        <button
          type="button"
          aria-label={'下载 ' + fileName}
          onClick={() => downloadMutation.mutate()}
          disabled={downloadMutation.isPending}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text disabled:opacity-50"
        >
          <Download className="h-3.5 w-3.5" />
        </button>
      </div>
      {downloadMutation.isError ? (
        <p className="mt-1 text-[11px] text-ha-danger">
          {downloadMutation.error.message}
        </p>
      ) : null}
      <ToneBadge tone={isImage ? 'success' : 'neutral'} className="mt-1.5">
        {artifact.kind}
      </ToneBadge>
    </li>
  );
}
