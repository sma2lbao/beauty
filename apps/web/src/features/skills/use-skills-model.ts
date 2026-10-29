/**
 * Skills 页数据与交互模型:技能/技能包查询,上传与删除变更,以及文件选择、
 * 版本展开、待删除包等局部状态。页面只负责布局与组合。
 *
 * - 顶部上传区:选择 SKILL.md zip → POST /api/v1/skills/packages,成功后刷新技能与包列表;
 * - 技能卡片标出来源(内置/上传)与是否含脚本;
 * - 「我的上传」按包列出最新版本/版本数/大小,可展开每个版本的运行时与入口,可删除(二次确认)。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import type { ChangeEvent } from 'react';

import { queryKeys } from '@/lib/query-keys';
import { skillsApi, type SkillPackage } from '@/lib/skills-api';

export function useSkillsModel() {
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

  return {
    skills,
    packages,
    skillsQuery,
    packagesQuery,
    fileInputRef,
    file,
    onPickFile,
    expandedPackageIds,
    toggleExpanded,
    pendingDelete,
    setPendingDelete,
    uploadMutation,
    deleteMutation,
    closeDeleteDialog,
  };
}

export type SkillsModel = ReturnType<typeof useSkillsModel>;
