/**
 * 顶部上传区:选择含 SKILL.md 的 zip 并上传技能包(结果/失败就地提示)。
 */
import { FileArchive, PackagePlus, Upload } from 'lucide-react';

import { ConsoleButton, ConsolePanel, Mono } from '@/components/console/ui';

import type { SkillsModel } from './use-skills-model';

export function SkillUploadPanel({ model }: { model: SkillsModel }) {
  const { fileInputRef, file, onPickFile, uploadMutation } = model;

  return (
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
  );
}
