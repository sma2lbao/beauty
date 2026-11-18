import { describe, expect, it } from 'vitest';

import { diffStats, parseUnifiedDiff, sideBySide } from './diff.js';

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
