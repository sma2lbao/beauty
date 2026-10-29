/**
 * 技能库区:标题/计数、读取失败提示、空态与技能卡片网格。
 */
import { ConsolePanel, EmptyPanel, Mono } from '@/components/console/ui';

import { SkillCard } from './skill-card';
import type { SkillsModel } from './use-skills-model';

export function SkillLibrarySection({ model }: { model: SkillsModel }) {
  const { skills, skillsQuery } = model;

  return (
    <>
      <div className="mt-6 flex items-center justify-between gap-3">
        <h2 className="text-[13px] font-semibold text-ha-text">技能库</h2>
        <Mono>{skills.length} 个可用技能</Mono>
      </div>

      {skillsQuery.isError ? (
        <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
          读取 Skills 失败:{(skillsQuery.error as Error).message}
        </ConsolePanel>
      ) : null}

      {skills.length === 0 && !skillsQuery.isPending && !skillsQuery.isError ? (
        <ConsolePanel className="mt-4">
          <EmptyPanel
            title="还没有可用技能"
            description="检查 skills/ 目录下的 manifest 是否存在且格式正确,或上传一个技能包。"
          />
        </ConsolePanel>
      ) : null}

      <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {skills.map((skill) => (
          <SkillCard key={skill.id} skill={skill} />
        ))}
      </div>
    </>
  );
}
