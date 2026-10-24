/**
 * 工作区控制台的数据层:workspace-service 的 API 契约(架构 §3/§7)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_WORKSPACE_API_URL`:后端基址;留空走同源(Vite dev 把
 *   `/workspace-api` 代理到 :8005,见 vite.config.ts)。
 */

import { authMode } from '@/lib/auth';
import { createHttpClient } from '@/lib/http';
import { workspaceBase as BASE } from '@/config/env';

const PREFIX = authMode() === 'session' ? '/api/workspace' : '/workspace-api';

export type Workspace = {
  id: string;
  name: string;
  description: string | null;
  status: string;
  /** 租户默认工作区:系统维护,可改名但不可删除。 */
  is_default?: boolean;
  created_at: string;
};

export type ArtifactMeta = {
  id: string;
  workspace_id: string;
  run_id: string | null;
  kind: string;
  title: string;
  /** 后端给出的落盘文件名(含扩展名);旧数据可能为空。 */
  filename?: string | null;
  /** 后端给出的 MIME;旧数据可能为空。 */
  content_type?: string | null;
  storage: string;
  size_bytes: number;
  /** 产物来源(如 report/tool/output);展示用,缺省不影响渲染。 */
  source?: string;
  created_at: string;
};

/** 详情响应:文本产物仍带 content;二进制文件可能没有正文。 */
export type Artifact = ArtifactMeta & { content?: string | null };

const http = createHttpClient(BASE);

export const workspaceApi = {
  workspaces: () => http.request<Workspace[]>(`${PREFIX}/v1/workspaces`),
  defaultWorkspace: () =>
    http.request<Workspace>(`${PREFIX}/v1/workspaces/default`),
  createWorkspace: (body: { name: string; description?: string | null }) =>
    http.request<Workspace>(`${PREFIX}/v1/workspaces`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /** 重命名工作区(只传 name 时不改描述)。 */
  renameWorkspace: (workspaceId: string, body: { name: string }) =>
    http.request<Workspace>(`${PREFIX}/v1/workspaces/${workspaceId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  /** 删除工作区:交付物一并删除;默认工作区后端拒绝(409)。 */
  removeWorkspace: (workspaceId: string) =>
    http.request<void>(`${PREFIX}/v1/workspaces/${workspaceId}`, {
      method: 'DELETE',
    }),
  artifacts: (workspaceId: string) =>
    http.request<ArtifactMeta[]>(
      `${PREFIX}/v1/workspaces/${workspaceId}/artifacts`,
    ),
  artifact: (artifactId: string) =>
    http.request<Artifact>(`${PREFIX}/v1/artifacts/${artifactId}`),
  /** 下载产物二进制(文本/图片/文档统一走这个端点,返回 Blob)。 */
  downloadArtifact: (artifactId: string): Promise<Blob> =>
    http.blob(`${PREFIX}/v1/artifacts/${artifactId}/download`),
  removeArtifact: (artifactId: string) =>
    http.request<void>(`${PREFIX}/v1/artifacts/${artifactId}`, {
      method: 'DELETE',
    }),
};
