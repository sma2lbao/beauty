/**
 * 右列:交付物预览(正文/图片)与下载。
 */
import { Download } from 'lucide-react';

import { ArtifactPreview } from '@/features/artifacts/artifact-preview';
import { ConsoleButton, ConsolePanel } from '@/components/console/ui';
import { artifactFileName } from '@/lib/artifacts';
import { formatSize } from '@/lib/presentation';

import type { WorkspacePageModel } from './use-workspace-page';

export function WorkspacePreviewPanel({
  model,
}: {
  model: WorkspacePageModel;
}) {
  const {
    selectedArtifact,
    selectedIsImage,
    imageUrl,
    imagePending,
    imageError,
    openArtifactMutation,
    downloadMutation,
  } = model;

  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ha-border px-4 py-2.5">
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-ha-text-2">产物预览</p>
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
                {imagePending
                  ? '读取中…'
                  : (imageError?.message ?? '图片读取失败。')}
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
  );
}
