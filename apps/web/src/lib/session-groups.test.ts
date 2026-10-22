import { describe, expect, it } from 'vitest';

import type { Conversation } from '@/lib/conversations-api';
import type { Workspace } from '@/lib/workspace-api';

import {
  UNGROUPED_SESSION_GROUP_KEY,
  UNGROUPED_SESSION_GROUP_NAME,
  groupSessionsByWorkspace,
} from './session-groups';

function workspace(id: string, name = '工作区 ' + id): Workspace {
  return {
    id,
    name,
    description: null,
    status: 'active',
    created_at: '2026-10-13T00:00:00+00:00',
  };
}

function session(id: string, workspaceId?: string | null): Conversation {
  return {
    id,
    title: id,
    workspace_id: workspaceId ?? null,
    created_at: '2026-10-13T10:00:00+00:00',
    updated_at: '2026-10-13T10:05:00+00:00',
  };
}

describe('groupSessionsByWorkspace', () => {
  it('组顺序跟随工作区清单,组内保持传入顺序', () => {
    const groups = groupSessionsByWorkspace(
      [session('a1', 'ws-a'), session('b1', 'ws-b'), session('a2', 'ws-a')],
      [workspace('ws-b', '销售资料'), workspace('ws-a', '默认工作区')],
    );

    expect(groups.map((group) => group.workspaceId)).toEqual(['ws-b', 'ws-a']);
    expect(groups.map((group) => group.name)).toEqual([
      '销售资料',
      '默认工作区',
    ]);
    // 服务端已按最近活跃倒序,分组不重新排序
    expect(groups[0]?.sessions.map((item) => item.id)).toEqual(['b1']);
    expect(groups[1]?.sessions.map((item) => item.id)).toEqual(['a1', 'a2']);
    // 没有关联不上的会话时不输出未分组组
    expect(groups).toHaveLength(2);
  });

  it('每个工作区都输出一组,0 会话的组保留', () => {
    const groups = groupSessionsByWorkspace(
      [session('a1', 'ws-a')],
      [workspace('ws-a'), workspace('ws-b')],
    );

    expect(groups).toHaveLength(2);
    expect(groups[0]?.sessions.map((item) => item.id)).toEqual(['a1']);
    expect(groups[1]?.sessions).toEqual([]);
  });

  it('归属为空或不在清单里的会话落「未分组」并排最后', () => {
    const groups = groupSessionsByWorkspace(
      [
        session('a1', 'ws-a'),
        session('legacy', null),
        session('ghost', 'ws-missing'),
      ],
      [workspace('ws-a'), workspace('ws-b')],
    );

    expect(groups.map((group) => group.workspaceId)).toEqual([
      'ws-a',
      'ws-b',
      null,
    ]);
    const ungrouped = groups[groups.length - 1];
    expect(ungrouped?.key).toBe(UNGROUPED_SESSION_GROUP_KEY);
    expect(ungrouped?.name).toBe(UNGROUPED_SESSION_GROUP_NAME);
    expect(ungrouped?.sessions.map((item) => item.id)).toEqual([
      'legacy',
      'ghost',
    ]);
  });

  it('没有工作区时:全部会话落未分组,空列表返回空数组', () => {
    const groups = groupSessionsByWorkspace(
      [session('a1', 'ws-a'), session('legacy', null)],
      [],
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]?.workspaceId).toBeNull();
    expect(groups[0]?.sessions.map((item) => item.id)).toEqual([
      'a1',
      'legacy',
    ]);

    expect(groupSessionsByWorkspace([], [])).toEqual([]);
    expect(groupSessionsByWorkspace([], [workspace('ws-a')])).toEqual([
      {
        key: 'ws-a',
        workspaceId: 'ws-a',
        name: '工作区 ws-a',
        sessions: [],
      },
    ]);
  });

  it('不改写入参', () => {
    const conversations = [session('a1', 'ws-a')];
    const workspaces = [workspace('ws-a')];

    groupSessionsByWorkspace(conversations, workspaces);

    expect(conversations).toEqual([session('a1', 'ws-a')]);
    expect(workspaces).toEqual([workspace('ws-a')]);
  });
});
