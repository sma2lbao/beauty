/**
 * Skills 页面:内置场景模板列表/详情,可从技能直接开始会话。
 */
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Wrench } from 'lucide-react';
import { Link } from 'react-router';

import {
  ConsolePanel,
  EmptyPanel,
  PageHeader,
  ToneBadge,
} from '@/components/console/ui';
import { skillsApi } from '@/lib/skills-api';

export function SkillsPage() {
  const skillsQuery = useQuery({
    queryKey: ['skills'],
    queryFn: skillsApi.list,
  });
  const skills = skillsQuery.data ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Skills"
        subtitle="场景模板:绑定系统提示词与工具白名单,选择后直接开始任务"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {skillsQuery.isError ? (
          <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
            读取 Skills 失败:{(skillsQuery.error as Error).message}
          </ConsolePanel>
        ) : null}

        {skills.length === 0 && !skillsQuery.isPending ? (
          <ConsolePanel className="mt-4">
            <EmptyPanel
              title="还没有可用技能"
              description="检查 skills/ 目录下的 manifest 是否存在且格式正确。"
            />
          </ConsolePanel>
        ) : null}

        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
                <ToneBadge tone="neutral">{skill.workspace_type}</ToneBadge>
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
      </div>
    </div>
  );
}
