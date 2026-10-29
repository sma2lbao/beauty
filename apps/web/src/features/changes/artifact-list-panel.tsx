/**
 * 产物列表(左栏):加载骨架、逐条选中与文件名/MIME/大小元信息。
 */
import { FileCode2 } from 'lucide-react';

import { ConsolePanel, ToneBadge } from '@/components/console/ui';
import { formatBytes } from '@/lib/presentation';
import { cn } from '@/lib/utils';

import type { ChangesModel } from './use-changes-model';

export function ArtifactListPanel({ model }: { model: ChangesModel }) {
  const { artifacts, artifactsPending, selected, setSelectedId } = model;

  return (
    <ConsolePanel className="hidden w-[300px] shrink-0 flex-col p-3 md:flex">
      <div className="flex items-center justify-between px-1.5 pb-2.5 pt-0.5">
        <span className="text-[13px] font-semibold text-ha-text">产物列表</span>
        <ToneBadge tone="neutral">{artifacts.length}</ToneBadge>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {artifactsPending
          ? [0, 1, 2, 3].map((key) => (
              <div
                key={key}
                className="h-12 animate-pulse rounded-md bg-ha-panel"
              />
            ))
          : null}
        {artifacts.map((artifact) => {
          const active = artifact.id === selected?.id;
          return (
            <button
              key={artifact.id}
              type="button"
              onClick={() => setSelectedId(artifact.id)}
              aria-pressed={active}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors',
                active ? 'bg-[#1c1d1f]' : 'hover:bg-ha-panel-2/60',
              )}
            >
              <FileCode2
                aria-hidden="true"
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  active ? 'text-ha-accent-soft' : 'text-ha-muted',
                )}
              />
              <span className="min-w-0 flex-1">
                <span
                  className={cn(
                    'block truncate font-mono text-[11px]',
                    active ? 'font-medium text-ha-text' : 'text-ha-text-2',
                  )}
                >
                  {artifact.filename ?? artifact.title}
                </span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-ha-faint">
                  {artifact.kind} · {formatBytes(artifact.size_bytes)}
                  {artifact.content_type ? ' · ' + artifact.content_type : null}
                </span>
              </span>
              {artifact.run_id ? (
                <span className="shrink-0 font-mono text-[10px] text-ha-faint">
                  #{artifact.run_id.slice(-4).toUpperCase()}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </ConsolePanel>
  );
}
