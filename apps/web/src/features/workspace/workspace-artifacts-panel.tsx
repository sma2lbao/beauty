/**
 * 右列:当前工作区的交付物清单(查看 / 删除)。
 */
import { Eye, Package, Trash2 } from 'lucide-react';

import { ConsoleButton, ConsolePanel, EmptyPanel } from '@/components/console/ui';
import { formatDateTime, formatSize } from '@/lib/presentation';

import { SectionHeading } from './section-heading';
import type { WorkspacePageModel } from './use-workspace-page';

export function WorkspaceArtifactsPanel({
  model,
}: {
  model: WorkspacePageModel;
}) {
  const { artifacts, artifactsLoading, openArtifactMutation, removeMutation } =
    model;

  return (
    <ConsolePanel
      id="workspace-artifacts"
      className="overflow-hidden scroll-mt-6"
    >
      <SectionHeading
        icon={<Package />}
        title="交付物"
        hint={`共 ${artifacts.length} 件`}
      />
      {artifactsLoading ? (
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
                  · {artifact.storage} · {formatSize(artifact.size_bytes)} ·{' '}
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
                  onClick={() => openArtifactMutation.mutate(artifact.id)}
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
  );
}
