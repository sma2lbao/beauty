import { describe, expect, it } from 'vitest';

import {
  diffStats,
  eventLine,
  liveRunText,
  parseUnifiedDiff,
  sideBySide,
  taskCode,
  toolCallsFrom,
} from './presentation';

const diff = [
  '@@ -42,7 +42,26 @@ export function sessionGuard',
  ' 40   import { resolveSession }',
  '- 41   const session = req.cookies?.sid;',
  '+ 42   const session = await resolveSession(req);',
  '  43   return next();',
].join('\n');

describe('parseUnifiedDiff', () => {
  it('普通正文不当作 diff', () => {
    expect(parseUnifiedDiff('# 周报\n\n- 日程已汇总')).toBeNull();
  });

  it('解析 hunk 与增删行,并维护两侧行号', () => {
    const rows = parseUnifiedDiff(diff);
    expect(rows).not.toBeNull();
    expect(rows?.map((row) => row.kind)).toEqual([
      'hunk',
      'context',
      'del',
      'add',
      'context',
    ]);
    const del = rows?.find((row) => row.kind === 'del');
    const add = rows?.find((row) => row.kind === 'add');
    // hunk 从 -42/+42 起;第一行上下文各占一行号,所以删除行是 43
    expect(del?.oldLine).toBe(43);
    expect(del?.newLine).toBeNull();
    expect(add?.oldLine).toBeNull();
    expect(add?.newLine).toBe(43);
    expect(diffStats(diff)).toEqual({ added: 1, removed: 1 });
  });

  it('并排视图把删除/新增配对到同一行', () => {
    const rows = sideBySide(parseUnifiedDiff(diff) ?? []);
    const pair = rows.find((row) => row.left?.kind === 'del');
    expect(pair?.right?.kind).toBe('add');
  });
});

describe('toolCallsFrom', () => {
  it('只挑工具调用事件并保留参数/结果', () => {
    const calls = toolCallsFrom([
      { seq: 1, type: 'run.created.v1', payload: {} },
      {
        seq: 2,
        type: 'run.tool.called.v1',
        payload: {
          name: 'read_file',
          arguments: '{"path":"a.ts"}',
          ok: true,
          detail: 'done',
        },
      },
    ]);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ seq: 2, name: 'read_file', ok: true });
    expect(calls[0].arguments).toContain('a.ts');
  });
});

describe('liveRunText', () => {
  it('没有 token.delta.v1 事件时返回 null', () => {
    expect(liveRunText([])).toBeNull();
    expect(
      liveRunText([
        { seq: 1, type: 'run.created.v1', payload: { attempt: 1 } },
        { seq: 2, type: 'run.planned.v1', payload: { attempt: 1 } },
      ]),
    ).toBeNull();
  });

  it('同一阶段按事件顺序拼接增量', () => {
    expect(
      liveRunText([
        {
          seq: 1,
          type: 'token.delta.v1',
          payload: { text: '正在', phase: 'execute' },
        },
        {
          seq: 2,
          type: 'token.delta.v1',
          payload: { text: '汇总', phase: 'execute' },
        },
      ]),
    ).toEqual({ phase: 'execute', text: '正在汇总' });
  });

  it('阶段切换只保留最后一段累积文本', () => {
    expect(
      liveRunText([
        {
          seq: 1,
          type: 'token.delta.v1',
          payload: { text: '规划草稿', phase: 'plan' },
        },
        {
          seq: 2,
          type: 'run.tool.called.v1',
          payload: { name: 'read_file', ok: true },
        },
        {
          seq: 3,
          type: 'token.delta.v1',
          payload: { text: '执行', phase: 'execute' },
        },
        {
          seq: 4,
          type: 'token.delta.v1',
          payload: { text: '完成', phase: 'execute' },
        },
      ]),
    ).toEqual({ phase: 'execute', text: '执行完成' });
  });
});

describe('eventLine', () => {
  it('失败事件给危险语气,普通规划事件给中性语气', () => {
    expect(
      eventLine({
        seq: 1,
        type: 'run.failed.v1',
        payload: { stage: 'execute', reason: 'boom' },
      }),
    ).toMatchObject({ tone: 'danger' });
    expect(
      eventLine({ seq: 2, type: 'run.planned.v1', payload: { attempt: 1 } }),
    ).toMatchObject({ tone: 'neutral' });
    expect(eventLine({ seq: 3, type: 'unknown.v1', payload: {} })).toBeNull();
  });
});

describe('taskCode', () => {
  it('从 run id 派生出稳定的任务编号', () => {
    expect(taskCode('run-1234')).toBe('#TASK-1234');
    expect(taskCode('')).toBe('#TASK-0000');
  });
});
