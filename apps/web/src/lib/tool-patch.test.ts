import { describe, expect, it } from 'vitest';

import { parseUnifiedDiff } from './presentation/diff';
import type { ToolCall } from './presentation/tools';
import { filePatchFromCalls, PATCH_TRUNCATED_NOTICE } from './tool-patch';

function call(overrides: Partial<ToolCall> & Pick<ToolCall, 'seq' | 'name'>): ToolCall {
  return { arguments: '{}', ok: true, detail: '', ...overrides };
}

describe('filePatchFromCalls', () => {
  it('write 把整文件内容重建为新增行,可被 diff 解析', () => {
    const result = filePatchFromCalls(
      [
        call({
          seq: 1,
          name: 'write',
          arguments: JSON.stringify({
            path: '/workspace/src/a.ts',
            content: 'const a = 1;\nconst b = 2;\n',
          }),
        }),
      ],
      '/workspace/src/a.ts',
    );

    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.patches).toHaveLength(1);
    const patch = result.patches[0];
    expect(patch?.diff).toContain('--- a/src/a.ts');
    expect(patch?.diff).toContain('+++ b/src/a.ts');
    expect(patch?.diff).toContain('@@ -0,0 +1,2 @@');
    expect(patch?.note).toBeNull();

    const rows = parseUnifiedDiff(patch?.diff ?? '') ?? [];
    expect(rows.filter((row) => row.kind === 'add')).toHaveLength(2);
  });

  it('write 空内容给「写入空文件」附注', () => {
    const result = filePatchFromCalls(
      [
        call({
          seq: 1,
          name: 'write',
          arguments: JSON.stringify({ path: 'empty.txt', content: '' }),
        }),
      ],
      '/workspace/empty.txt',
    );

    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.patches[0]?.note).toBe('写入空文件');
  });

  it('edit 重建删除/新增行,replace_all 在附注里说明', () => {
    const result = filePatchFromCalls(
      [
        call({
          seq: 2,
          name: 'edit',
          arguments: JSON.stringify({
            path: '/workspace/src/a.ts',
            old_string: 'old line',
            new_string: 'new line',
            replace_all: true,
          }),
        }),
      ],
      'src/a.ts',
    );

    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const patch = result.patches[0];
    expect(patch?.diff).toContain('-old line');
    expect(patch?.diff).toContain('+new line');
    expect(patch?.note).toBe('替换全部匹配');

    const rows = parseUnifiedDiff(patch?.diff ?? '') ?? [];
    expect(rows.some((row) => row.kind === 'del')).toBe(true);
    expect(rows.some((row) => row.kind === 'add')).toBe(true);
  });

  it('edit 纯新增/纯删除给对应附注', () => {
    const added = filePatchFromCalls(
      [
        call({
          seq: 1,
          name: 'edit',
          arguments: JSON.stringify({
            path: '/workspace/a.ts',
            old_string: '',
            new_string: 'new',
          }),
        }),
      ],
      '/workspace/a.ts',
    );
    const removed = filePatchFromCalls(
      [
        call({
          seq: 2,
          name: 'edit',
          arguments: JSON.stringify({
            path: '/workspace/a.ts',
            old_string: 'gone',
            new_string: '',
          }),
        }),
      ],
      '/workspace/a.ts',
    );

    expect(added.status).toBe('ok');
    if (added.status === 'ok') expect(added.patches[0]?.note).toContain('新增内容');
    expect(removed.status).toBe('ok');
    if (removed.status === 'ok') expect(removed.patches[0]?.note).toContain('删除内容');
  });

  it('按 seq 升序保留同一文件的多次改写', () => {
    const result = filePatchFromCalls(
      [
        call({
          seq: 5,
          name: 'edit',
          arguments: JSON.stringify({
            path: '/workspace/a.ts',
            old_string: 'b',
            new_string: 'c',
          }),
        }),
        call({
          seq: 1,
          name: 'write',
          arguments: JSON.stringify({ path: '/workspace/a.ts', content: 'a' }),
        }),
      ],
      '/workspace/a.ts',
    );

    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.patches.map((item) => item.seq)).toEqual([1, 5]);
  });

  it('参数被截断时整体降级并保留原始文本', () => {
    const result = filePatchFromCalls(
      [
        call({
          seq: 3,
          name: 'write',
          arguments: '{"path":"/workspace/a.ts","content":"const a',
        }),
      ],
      '/workspace/a.ts',
    );

    expect(result).toEqual({
      status: 'truncated',
      items: [
        {
          seq: 3,
          toolName: 'write',
          raw: '{"path":"/workspace/a.ts","content":"const a',
        },
      ],
    });
    expect(PATCH_TRUNCATED_NOTICE).toBe('参数被截断，无法重建补丁');
  });

  it('只收 write/edit,且路径要匹配目标文件', () => {
    const result = filePatchFromCalls(
      [
        call({ seq: 1, name: 'read', arguments: '{"path":"/workspace/a.ts"}' }),
        call({
          seq: 2,
          name: 'write',
          arguments: JSON.stringify({ path: '/workspace/b.ts', content: 'b' }),
        }),
        call({
          seq: 3,
          name: 'write',
          arguments: JSON.stringify({ path: '/workspace/a.ts', content: 'a' }),
        }),
      ],
      '/workspace/a.ts',
    );

    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.patches).toHaveLength(1);
    expect(result.patches[0]?.seq).toBe(3);
  });

  it('没有任何相关调用时返回 none', () => {
    expect(filePatchFromCalls([], '/workspace/a.ts')).toEqual({ status: 'none' });
    expect(
      filePatchFromCalls(
        [call({ seq: 1, name: 'grep', arguments: '{"pattern":"x"}' })],
        '/workspace/a.ts',
      ),
    ).toEqual({ status: 'none' });
  });
});
