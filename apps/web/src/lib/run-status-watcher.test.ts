import { describe, expect, it } from 'vitest';

import type { Run, RunStatus } from '@/lib/runs-api';
import {
  pushRunNotifications,
  pushServerNotification,
  statusChanges,
} from './run-status-watcher';

function run(id: string, status: RunStatus, conversationId: string | null): Run {
  return {
    id,
    title: '整理本周邮件为周报',
    goal: '整理本周邮件为周报',
    status,
    rounds: 0,
    attempts: 1,
    acceptance_criteria: [],
    status_reason: null,
    parent_run_id: null,
    conversation_id: conversationId,
    created_at: '2026-10-18T00:00:00Z',
  };
}

describe('statusChanges', () => {
  it('只报告已知 run 的状态变化,新 run 只建基线', () => {
    const changes = statusChanges(
      new Map([
        ['r1', 'running' as RunStatus],
        ['r2', 'completed' as RunStatus],
      ]),
      [run('r1', 'completed', 'c1'), run('r3', 'pending', null)],
    );
    expect(changes).toEqual([
      {
        runId: 'r1',
        conversationId: 'c1',
        from: 'running',
        to: 'completed',
      },
    ]);
  });
});

describe('pushRunNotifications', () => {
  it('终态与待审批/待确认推通知,普通中间态不打扰', () => {
    const pushed: { tone: string; title: string }[] = [];
    const push = (input: { tone: string; title: string }) => {
      pushed.push({ tone: input.tone, title: input.title });
    };
    const runs = new Map([
      ['r1', run('r1', 'completed', 'c1')],
      ['r2', run('r2', 'running', 'c1')],
    ]);
    pushRunNotifications(
      [
        { runId: 'r1', conversationId: 'c1', from: 'running', to: 'completed' },
        { runId: 'r2', conversationId: 'c1', from: 'pending', to: 'running' },
      ],
      runs,
      push,
      null,
    );
    expect(pushed).toEqual([{ tone: 'success', title: '任务已完成' }]);
  });

  it('当前会话内的变化只进列表(toast 关闭),描述与跳转地址正确', () => {
    const pushed: {
      tone: string;
      title: string;
      description?: string;
      to?: string;
      ttlMs?: number;
    }[] = [];
    const push = (input: {
      tone: string;
      title: string;
      description?: string;
      to?: string;
      ttlMs?: number;
    }) => pushed.push(input);
    const runs = new Map([
      ['r1', run('r1', 'failed', 'c9')],
      ['r2', run('r2', 'completed', null)],
    ]);
    pushRunNotifications(
      [
        { runId: 'r1', conversationId: 'c9', from: 'running', to: 'failed' },
        { runId: 'r2', conversationId: null, from: 'running', to: 'completed' },
      ],
      runs,
      push,
      'c9',
    );
    expect(pushed).toHaveLength(2);
    expect(pushed[0]).toMatchObject({
      tone: 'danger',
      title: '任务失败',
      description: '整理本周邮件为周报',
      to: '/?conversation=c9',
      ttlMs: 0,
    });
    expect(pushed[1]).toMatchObject({
      tone: 'success',
      to: '/runs',
    });
    expect(pushed[1].ttlMs).toBeUndefined();
  });
});
describe('pushServerNotification', () => {
  const collect = () => {
    const pushed: {
      tone: string;
      title: string;
      description?: string;
      to?: string;
      ttlMs?: number;
    }[] = [];
    return {
      pushed,
      push: (input: {
        tone: string;
        title: string;
        description?: string;
        to?: string;
        ttlMs?: number;
      }) => pushed.push(input),
    };
  };

  it('需要提醒的状态推通知,中间态忽略', () => {
    const { pushed, push } = collect();
    const base = {
      run_id: 'r1',
      conversation_id: 'c1',
      title: '周报',
      goal: '把邮件整理成周报',
      updated_at: null,
    };
    expect(pushServerNotification({ ...base, status: 'running' }, push, null))
      .toBe(false);
    expect(pushed).toHaveLength(0);
    expect(pushServerNotification({ ...base, status: 'completed' }, push, null))
      .toBe(true);
    expect(pushed[0]).toMatchObject({
      tone: 'success',
      title: '任务已完成',
      to: '/?conversation=c1',
    });
  });

  it('当前会话内的变化只进列表(ttlMs=0)', () => {
    const { pushed, push } = collect();
    pushServerNotification(
      {
        run_id: 'r1',
        conversation_id: 'c9',
        title: '周报',
        goal: '把邮件整理成周报',
        status: 'failed',
        updated_at: null,
      },
      push,
      'c9',
    );
    expect(pushed[0]).toMatchObject({ tone: 'danger', ttlMs: 0 });
  });

  it('没有会话归属时跳到任务编排页', () => {
    const { pushed, push } = collect();
    pushServerNotification(
      {
        run_id: 'r2',
        conversation_id: null,
        title: 'x',
        goal: 'y',
        status: 'blocked',
        updated_at: null,
      },
      push,
      null,
    );
    expect(pushed[0].to).toBe('/runs');
  });
});
