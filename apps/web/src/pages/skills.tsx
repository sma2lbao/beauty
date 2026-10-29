/**
 * Skills 页面:只做组装(页头 + 上传区 + 技能库 + 我的上传 + 删除确认弹窗)。
 *
 * 数据查询与变更、局部状态在 features/skills/use-skills-model.ts;
 * 上传区、技能卡片、包清单与删除弹窗在 features/skills/ 下按职责拆分。
 */
import { PageHeader } from '@/components/console/ui';
import { SkillDeleteDialog } from '@/features/skills/skill-delete-dialog';
import { SkillLibrarySection } from '@/features/skills/skill-library-section';
import { SkillPackagesSection } from '@/features/skills/skill-packages-section';
import { SkillUploadPanel } from '@/features/skills/skill-upload-panel';
import { useSkillsModel } from '@/features/skills/use-skills-model';

export function SkillsPage() {
  const model = useSkillsModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Skills"
        subtitle="场景模板与上传技能包:绑定系统提示词与工具白名单,选择后直接开始任务"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {/* 上传技能包 */}
        <SkillUploadPanel model={model} />
        <SkillLibrarySection model={model} />
        <SkillPackagesSection model={model} />
      </div>

      <SkillDeleteDialog model={model} />
    </div>
  );
}
