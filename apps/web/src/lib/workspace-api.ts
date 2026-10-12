/**
 * 工作区控制台的数据层:workspace-service 的 API 契约(架构 §3/§7)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_WORKSPACE_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/workspace-api` 代理到 :8005,见 vite.config.ts)。
 */

import { requestJson } from '@/lib/http';

const BASE = (import.meta.env.VITE_WORKSPACE_API_URL as string | undefined) ?? '';

const PREFIX = '/workspace-api';

export type Workspace = {
  id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string;
};

export type ArtifactMeta = {
  id: string;
  workspace_id: string;
  run_id: string | null;
  kind: string;
  title: string;
  storage: string;
  size_bytes: number;
  created_at: string;
};

export type Artifact = ArtifactMeta & { content: string };

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(BASE, `${PREFIX}${path}`, init);
}

export const workspaceApi = {
  workspaces: () => request<Workspace[]>('/v1/workspaces'),
  defaultWorkspace: () => request<Workspace>('/v1/workspaces/default'),
  createWorkspace: (body: { name: string; description?: string | null }) =>
    request<Workspace>('/v1/workspaces', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  artifacts: (workspaceId: string) =>
    request<ArtifactMeta[]>(`/v1/workspaces/${workspaceId}/artifacts`),
  artifact: (artifactId: string) =>
    request<Artifact>(`/v1/artifacts/${artifactId}`),
  removeArtifact: (artifactId: string) =>
    request<void>(`/v1/artifacts/${artifactId}`, { method: 'DELETE' }),
};
