/**
 * Skills 页面:内置场景模板 + 上传的技能包,可从技能直接开始会话。
 *
 * - 顶部上传区:选择 SKILL.md zip → POST /api/v1/skills/packages,成功后刷新技能与包列表;
 * - 技能卡片标出来源(内置/上传)与是否含脚本;
 * - 「我的上传」按包列出最新版本/版本数/大小,可展开每个版本的运行时与入口,可删除(二次确认)。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  FileArchive,
  PackagePlus,
  Trash2,
  Upload,
  Wrench,
} from 'lucide-react';
import { useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { Link } from 'react-router';

import { Dialog } from '@beauty/design';

import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  Mono,
  PageHeader,
  ToneBadge,
} from '@/components/console/ui';
import { formatBytes } from '@/lib/presentation';
import { skillsApi } from '@/lib/skills-api';
import type { SkillPackage } from '@/lib/skills-api';
import { queryKeys } from '@/lib/query-keys';

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString('zh-CN');
}

export function SkillsPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [expandedPackageIds, setExpandedPackageIds] = useState<string[]>([]);
  const [pendingDelete, setPendingDelete] = useState<SkillPackage | null>(null);

  const skillsQuery = useQuery({
    queryKey: queryKeys.skills,
    queryFn: skillsApi.list,
  });
  const packagesQuery = useQuery({
    queryKey: queryKeys.skillPackages,
    queryFn: skillsApi.listPackages,
  });
  const skills = skillsQuery.data ?? [];
  const packages = packagesQuery.data ?? [];

  const refreshSkills = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.skills });
  const refreshPackages = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.skillPackages });

  const uploadMutation = useMutation({
    mutationFn: (target: File) => skillsApi.uploadPackage(target),
    onSuccess: async () => {
      // 上传成功后清掉已选文件,避免重复提交同一个 zip
      setFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      await Promise.all([refreshSkills(), refreshPackages()]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (packageId: string) => skillsApi.deletePackage(packageId),
    onSuccess: async () => {
      setPendingDelete(null);
      await Promise.all([refreshSkills(), refreshPackages()]);
    },
  });

  const onPickFile = (event: ChangeEvent<HTMLInputElement>) => {
    uploadMutation.reset();
    setFile(event.target.files?.[0] ?? null);
  };

  const toggleExpanded = (packageId: string) => {
    setExpandedPackageIds((current) =>
      current.includes(packageId)
        ? current.filter((id) => id !== packageId)
        : [...current, packageId],
    );
  };

  const closeDeleteDialog = () => {
    setPendingDelete(null);
    deleteMutation.reset();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Skills"
        subtitle="场景模板与上传技能包:绑定系统提示词与工具白名单,选择后直接开始任务"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {/* 上传技能包 */}
        <ConsolePanel className="mt-4 border-dashed p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-ha-text">
                <PackagePlus className="h-3.5 w-3.5 text-ha-muted" />
                上传技能包
              </h2>
              <p className="mt-1 max-w-xl text-[12px] leading-5 text-ha-dim">
                把含 SKILL.md 的技能目录打成 zip 后上传;服务端校验 manifest
                并生成新的技能版本,同一技能重复上传会累积版本。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileInputRef}
                id="skill-package-file"
                type="file"
                accept=".zip,application/zip"
                className="hidden"
                onChange={onPickFile}
              />
              <ConsoleButton
                variant="secondary"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                <FileArchive className="h-3 w-3" />
                选择 zip
              </ConsoleButton>
              <Mono className="max-w-[220px] truncate">
                {file ? file.name : '未选择文件'}
              </Mono>
              <ConsoleButton
                variant="primary"
                size="sm"
                disabled={!file || uploadMutation.isPending}
                onClick={() => {
                  if (file) uploadMutation.mutate(file);
                }}
              >
                <Upload className="h-3 w-3" />
                {uploadMutation.isPending ? '上传中…' : '开始上传'}
              </ConsoleButton>
            </div>
          </div>
          {uploadMutation.isError ? (
            <p role="alert" className="mt-2 text-[12px] text-ha-danger">
              上传失败:{uploadMutation.error.message}
            </p>
          ) : null}
          {uploadMutation.isSuccess ? (
            <p role="status" className="mt-2 text-[12px] text-ha-success">
              已上传 {uploadMutation.data.title || uploadMutation.data.slug}
              {uploadMutation.data.latest_version
                ? ` v${uploadMutation.data.latest_version}`
                : ''}
            </p>
          ) : null}
        </ConsolePanel>

        {skillsQuery.isError ? (
          <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
            读取 Skills 失败:{(skillsQuery.error as Error).message}
          </ConsolePanel>
        ) : null}

        <div className="mt-6 flex items-center justify-between gap-3">
          <h2 className="text-[13px] font-semibold text-ha-text">技能库</h2>
          <Mono>{skills.length} 个可用技能</Mono>
        </div>

        {skills.length === 0 &&
        !skillsQuery.isPending &&
        !skillsQuery.isError ? (
          <ConsolePanel className="mt-4">
            <EmptyPanel
              title="还没有可用技能"
              description="检查 skills/ 目录下的 manifest 是否存在且格式正确,或上传一个技能包。"
            />
          </ConsolePanel>
        ) : null}

        <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((skill) => (
            <ConsolePanel key={skill.id} className="flex flex-col p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="truncate text-[14px] font-semibold text-ha-text">
                    {skill.title}
                  </h2>
                  <p className="mt-1 truncate font-mono text-[11px] text-ha-faint">
                    {skill.id} · v{skill.version}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                  {skill.has_scripts ? (
                    <ToneBadge tone="warning">含脚本</ToneBadge>
                  ) : null}
                  <ToneBadge
                    tone={skill.source === 'uploaded' ? 'info' : 'neutral'}
                  >
                    {skill.source === 'uploaded' ? '上传' : '内置'}
                  </ToneBadge>
                  <ToneBadge tone="neutral">{skill.workspace_type}</ToneBadge>
                </div>
              </div>
              <p className="mt-2 line-clamp-3 text-[12px] leading-5 text-ha-muted">
                {skill.description || '暂无描述'}
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {skill.tools.length ? (
                  skill.tools.map((tool) => (
                    <span
                      key={tool}
                      className="inline-flex items-center gap-1 rounded border border-ha-border bg-ha-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-ha-muted"
                    >
                      <Wrench className="h-2.5 w-2.5" />
                      {tool}
                    </span>
                  ))
                ) : (
                  <span className="font-mono text-[10px] text-ha-dim">
                    无工具 / 只读
                  </span>
                )}
              </div>
              <div className="mt-auto flex items-center justify-between gap-2 pt-4">
                <ToneBadge tone="success">
                  {skill.deliverables[0]?.kind ?? 'markdown'}
                </ToneBadge>
                <Link
                  to={'/?skill=' + encodeURIComponent(skill.id)}
                  className="inline-flex h-7 items-center gap-1 rounded-md border border-ha-border-strong bg-ha-panel-2 px-2.5 text-[12px] text-ha-text-2 transition-colors hover:text-ha-text"
                >
                  用此技能开始
                  <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </ConsolePanel>
          ))}
        </div>

        {/* 我的上传 */}
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
      </div>

      {pendingDelete ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) closeDeleteDialog();
          }}
          title="删除技能包"
          description={`将删除「${pendingDelete.title || pendingDelete.slug}」及其 ${
            pendingDelete.versions.length
          } 个版本,操作不可恢复。`}
          footer={
            <>
              <ConsoleButton
                variant="secondary"
                size="md"
                onClick={closeDeleteDialog}
              >
                取消
              </ConsoleButton>
              <ConsoleButton
                variant="danger"
                size="md"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(pendingDelete.id)}
              >
                {deleteMutation.isPending ? '删除中…' : '确认删除'}
              </ConsoleButton>
            </>
          }
        >
          {deleteMutation.isError ? (
            <p className="text-[12px] text-ha-danger">
              删除失败:{deleteMutation.error.message}
            </p>
          ) : null}
        </Dialog>
      ) : null}
    </div>
  );
}
