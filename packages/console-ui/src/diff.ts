/**
 * unified diff 解析与并排视图(Diff 查看器使用),纯函数、无 IO。
 *
 * 从 apps/web/src/lib/presentation/diff.ts 迁入:解析逻辑与渲染同属展示层,
 * 一起放共享包,避免只搬渲染、解析仍留在 web。
 */

export type DiffRowKind = 'hunk' | 'add' | 'del' | 'context';

export type DiffRow = {
  kind: DiffRowKind;
  text: string;
  oldLine: number | null;
  newLine: number | null;
};

/**
 * 解析 unified diff(---/+++/@@ 或仅 @@ 的片段)。
 * 不是 diff 时返回 null,调用方回退成普通代码预览。
 */
export function parseUnifiedDiff(content: string): DiffRow[] | null {
  const lines = content.split('\n');
  const looksLikeDiff =
    lines.some((line) => line.startsWith('@@')) ||
    (lines.some((line) => line.startsWith('--- ')) &&
      lines.some((line) => line.startsWith('+++ ')));
  if (!looksLikeDiff) return null;

  const rows: DiffRow[] = [];
  let oldLine = 0;
  let newLine = 0;
  for (const line of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: 'hunk', text: line, oldLine: null, newLine: null });
      continue;
    }
    if (
      line.startsWith('diff ') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('new file') ||
      line.startsWith('deleted file')
    ) {
      rows.push({ kind: 'hunk', text: line, oldLine: null, newLine: null });
      continue;
    }
    if (line.startsWith('+')) {
      rows.push({ kind: 'add', text: line, oldLine: null, newLine });
      newLine += 1;
      continue;
    }
    if (line.startsWith('-')) {
      rows.push({ kind: 'del', text: line, oldLine, newLine: null });
      oldLine += 1;
      continue;
    }
    rows.push({ kind: 'context', text: line, oldLine, newLine });
    oldLine += 1;
    newLine += 1;
  }
  return rows;
}

export type SideBySideRow = {
  kind: DiffRowKind;
  left: DiffRow | null;
  right: DiffRow | null;
};

/** 并排视图:把连续的删除/新增配对到同一行,上下文行两侧都显示。 */
export function sideBySide(rows: readonly DiffRow[]): SideBySideRow[] {
  const result: SideBySideRow[] = [];
  let index = 0;
  while (index < rows.length) {
    const row = rows[index];
    if (row.kind === 'hunk' || row.kind === 'context') {
      result.push({ kind: row.kind, left: row, right: row });
      index += 1;
      continue;
    }
    const removed: DiffRow[] = [];
    const added: DiffRow[] = [];
    while (index < rows.length && rows[index].kind === 'del') {
      removed.push(rows[index]);
      index += 1;
    }
    while (index < rows.length && rows[index].kind === 'add') {
      added.push(rows[index]);
      index += 1;
    }
    const length = Math.max(removed.length, added.length);
    for (let offset = 0; offset < length; offset += 1) {
      result.push({
        kind: removed[offset] ? 'del' : 'add',
        left: removed[offset] ?? null,
        right: added[offset] ?? null,
      });
    }
  }
  return result;
}

/** 计数:页面顶栏要显示 +N -M。不是 diff 时返回 null。 */
export function diffStats(
  content: string,
): { added: number; removed: number } | null {
  const rows = parseUnifiedDiff(content);
  if (!rows) return null;
  return {
    added: rows.filter((row) => row.kind === 'add').length,
    removed: rows.filter((row) => row.kind === 'del').length,
  };
}
