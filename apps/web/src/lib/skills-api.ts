/**
 * Skills 数据层:内置 manifest 由 orchestrator 启动时加载。
 */
import { requestJson } from '@/lib/http';

export type SkillDeliverable = {
  kind: string;
  template?: string | null;
};

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
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>('', '/api/v1/skills' + path, init);
}

export const skillsApi = {
  list: () => request<SkillManifest[]>(''),
  get: (skillId: string) => request<SkillManifest>('/' + skillId),
};
