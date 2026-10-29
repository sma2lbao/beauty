/**
 * 技能卡片:标题/版本、来源与含脚本徽章、工具白名单、「用此技能开始」入口。
 */
import { ArrowRight, Wrench } from 'lucide-react';
import { Link } from 'react-router';

import { ConsolePanel, ToneBadge } from '@/components/console/ui';
import type { SkillManifest } from '@/lib/skills-api';

export function SkillCard({ skill }: { skill: SkillManifest }) {
  return (
    <ConsolePanel className="flex flex-col p-4">
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
          <ToneBadge tone={skill.source === 'uploaded' ? 'info' : 'neutral'}>
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
  );
}
