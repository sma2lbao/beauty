/**
 * 侧栏会话分组:按工作区把会话归到各自组里。
 *
 * 服务端列表返回的是租户内全部会话(updated_at 倒序),这里只做客户端分组:
 * - 组顺序 = 工作区清单顺序,每个工作区都输出一组(即使 0 会话,组头还要能新建会话);
 * - 组内保持传入顺序(服务端已按最近活跃排序),不在这里重新排序;
 * - 归属为空、或指向已不在清单里的工作区的会话,统一落到最后的「未分组」组。
 */
import type { Conversation } from '@/lib/conversations-api';
import type { Workspace } from '@/lib/workspace-api';

export type SessionGroup = {
  /** 稳定 key:工作区 id,或未分组组的固定值 */
  key: string;
  /** 工作区 id;未分组组为 null */
  workspaceId: string | null;
  name: string;
  sessions: Conversation[];
};

export const UNGROUPED_SESSION_GROUP_KEY = 'ungrouped';
export const UNGROUPED_SESSION_GROUP_NAME = '未分组';

/**
 * 把租户全部会话按工作区切成侧栏要渲染的组。
 * 纯函数,不依赖 React,也不改写入参。
 */
export function groupSessionsByWorkspace(
  conversations: readonly Conversation[],
  workspaces: readonly Workspace[],
): SessionGroup[] {
  const knownWorkspaces = workspaces.filter(Boolean);
  const sessionsByWorkspace = new Map<string, Conversation[]>(
    knownWorkspaces.map((workspace) => [workspace.id, []]),
  );
  const ungrouped: Conversation[] = [];

  for (const conversation of conversations) {
    const workspaceId = conversation.workspace_id ?? null;
    const bucket = workspaceId
      ? sessionsByWorkspace.get(workspaceId)
      : undefined;
    if (bucket) {
      bucket.push(conversation);
    } else {
      ungrouped.push(conversation);
    }
  }

  const groups: SessionGroup[] = knownWorkspaces.map((workspace) => ({
    key: workspace.id,
    workspaceId: workspace.id,
    name: workspace.name,
    sessions: sessionsByWorkspace.get(workspace.id) ?? [],
  }));

  if (ungrouped.length > 0) {
    groups.push({
      key: UNGROUPED_SESSION_GROUP_KEY,
      workspaceId: null,
      name: UNGROUPED_SESSION_GROUP_NAME,
      sessions: ungrouped,
    });
  }

  return groups;
}
