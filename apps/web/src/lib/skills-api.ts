/**
 * Skills 数据层:内置 manifest + 上传的技能包(同源 /api/v1/skills,BFF 透传)。
 *
 * - 内置技能由 orchestrator 启动时从 skills/ 目录加载;
 * - 上传技能包(SKILL.md zip)在服务端落成"包 + 版本",列表接口把包的最新版本
 *   合并进 GET /api/v1/skills,用 `source` / `package_id` 标注来源。
 */
import { requestJson } from '@/lib/http';

export type SkillDeliverable = {
  kind: string;
  template?: string | null;
};

/** 技能来源:内置目录,或用户上传的技能包。 */
export type SkillSource = 'builtin' | 'uploaded';

export type SkillManifest = {
  id: string;
  version: string;
  title: string;
  description: string;
  workspace_type: string;
  tools: string[];
  permissions: Record<string, string>;
  system_prompt: string;
  input_schema: Record<string, unknown>;
  deliverables: SkillDeliverable[];
  triggers: string[];
  /** 来源标注;旧版服务可能不返回,前端按内置处理 */
  source?: SkillSource;
  /** 技能包是否含 scripts/(含脚本需沙箱执行) */
  has_scripts?: boolean;
  /** 上传技能所属的包 id;内置技能为 null/缺省 */
  package_id?: string | null;
  /** 技能对应的包版本 id;内置技能为 null/缺省 */
  version_id?: string | null;
};

/** 技能包的一个版本(上传即生成新版本;可选项来自 manifest 解析结果)。 */
export type SkillVersion = {
  id: string;
  version: string;
  size_bytes: number;
  sha256: string;
  /** 技能入口文件(SKILL.md 等),相对包根;未声明时为 null */
  entrypoint: string[] | null;
  /** 沙箱运行时;未声明时为 null */
  runtime: string | null;
  /** 网络访问策略;未声明时为 null */
  network: string | null;
  timeout_seconds: number | null;
  has_scripts: boolean;
  created_at: string;
};

/** 上传的技能包:一个包可累积多个版本,latest_version 指向最新版本号。 */
export type SkillPackage = {
  id: string;
  slug: string;
  title: string;
  description: string;
  /** 最新版本号;还没有成功解析的版本时为 null */
  latest_version: string | null;
  created_at: string;
  updated_at: string;
  versions: SkillVersion[];
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>('', '/api/v1/skills' + path, init);
}

export const skillsApi = {
  list: () => request<SkillManifest[]>(''),
  get: (skillId: string) => request<SkillManifest>('/' + skillId),
  listPackages: () => request<SkillPackage[]>('/packages'),
  getPackage: (packageId: string) =>
    request<SkillPackage>('/packages/' + packageId),
  /** 上传 SKILL.md zip 包:multipart 的 file 字段,不手写 Content-Type。 */
  uploadPackage: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<SkillPackage>('/packages', {
      method: 'POST',
      body: form,
    });
  },
  deletePackage: (packageId: string) =>
    request<void>('/packages/' + packageId, { method: 'DELETE' }),
};
