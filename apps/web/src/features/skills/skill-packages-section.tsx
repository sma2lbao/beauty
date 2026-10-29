/**
 * 「我的上传」区:技能包清单,可展开版本详情、发起删除(确认弹窗见 delete-dialog)。
 */
import { ChevronDown, ChevronUp, PackagePlus, Trash2 } from 'lucide-react';

import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  Mono,
  ToneBadge,
} from '@/components/console/ui';
import { formatBytes } from '@/lib/presentation';

import { formatDate } from './display';
import type { SkillsModel } from './use-skills-model';

export function SkillPackagesSection({ model }: { model: SkillsModel }) {
  const {
    packages,
    packagesQuery,
    expandedPackageIds,
    toggleExpanded,
    setPendingDelete,
  } = model;

  return (
    <>
      <div className="mt-8 flex items-center justify-between gap-3">
        <h2 className="text-[13px] font-semibold text-ha-text">我的上传</h2>
        <Mono>{packages.length} 个技能包</Mono>
      </div>

      {packagesQuery.isError ? (
        <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
          读取上传记录失败:{(packagesQuery.error as Error).message}
        </ConsolePanel>
      ) : null}

      {packages.length === 0 &&
      !packagesQuery.isPending &&
      !packagesQuery.isError ? (
        <ConsolePanel className="mt-3">
          <EmptyPanel
            icon={<PackagePlus className="h-5 w-5" />}
            title="还没有上传技能包"
            description="把含 SKILL.md 的目录打成 zip,用上方「上传技能包」提交;解析成功后这里会出现包与版本。"
          />
        </ConsolePanel>
      ) : null}

      {packages.length ? (
        <ul className="mt-3 space-y-2">
          {packages.map((item) => {
            const expanded = expandedPackageIds.includes(item.id);
            const latest =
              item.versions.find(
                (version) => version.version === item.latest_version,
              ) ?? item.versions[0];
            return (
              <li
                key={item.id}
                className="rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 text-[13px] font-medium text-ha-text-2">
                      <span className="truncate">
                        {item.title || item.slug}
                      </span>
                      <ToneBadge
                        tone={item.latest_version ? 'info' : 'neutral'}
                      >
                        {item.latest_version
                          ? `v${item.latest_version}`
                          : '无版本'}
                      </ToneBadge>
                      <span className="truncate font-mono text-[11px] text-ha-dim">
                        {item.slug}
                      </span>
                    </p>
                    {item.description ? (
                      <p className="mt-1 line-clamp-1 text-[12px] text-ha-muted">
                        {item.description}
                      </p>
                    ) : null}
                    <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-ha-dim">
                      <span>{item.versions.length} 个版本</span>
                      <span>·</span>
                      <span>
                        {latest ? formatBytes(latest.size_bytes) : '—'}
                      </span>
                      <span>·</span>
                      <span>更新于 {formatDate(item.updated_at)}</span>
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <ConsoleButton
                      variant="ghost"
                      size="sm"
                      aria-expanded={expanded}
                      onClick={() => toggleExpanded(item.id)}
                    >
                      {expanded ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : (
                        <ChevronDown className="h-3 w-3" />
                      )}
                      版本({item.versions.length})
                    </ConsoleButton>
                    <ConsoleButton
                      variant="danger"
                      size="sm"
                      onClick={() => setPendingDelete(item)}
                    >
                      <Trash2 className="h-3 w-3" />
                      删除
                    </ConsoleButton>
                  </div>
                </div>

                {expanded ? (
                  <ul className="mt-2 space-y-1.5 border-t border-ha-border pt-2">
                    {item.versions.map((version) => (
                      <li
                        key={version.id}
                        className="rounded border border-ha-border bg-ha-panel px-2.5 py-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-ha-muted">
                          <span className="text-ha-text-2">
                            v{version.version}
                          </span>
                          <span>{formatBytes(version.size_bytes)}</span>
                          <span>sha256 {version.sha256.slice(0, 12)}</span>
                          {version.runtime ? (
                            <span>runtime {version.runtime}</span>
                          ) : null}
                          {version.network ? (
                            <span>network {version.network}</span>
                          ) : null}
                          {typeof version.timeout_seconds === 'number' ? (
                            <span>timeout {version.timeout_seconds}s</span>
                          ) : null}
                          {version.has_scripts ? (
                            <ToneBadge tone="warning">含脚本</ToneBadge>
                          ) : null}
                        </div>
                        <p className="mt-1 truncate font-mono text-[11px] text-ha-dim">
                          entrypoint{' '}
                          {version.entrypoint?.length
                            ? version.entrypoint.join(', ')
                            : 'SKILL.md'}
                        </p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </>
  );
}
