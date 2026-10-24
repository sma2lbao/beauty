/**
 * W2 R-21:Diff 查看器渲染契约。
 * - 带 unified diff 标记时按增删行渲染,行号可读;
 * - 无 diff 标记时回退成代码预览(不为好看伪造 diff)。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { parseUnifiedDiff } from '@/lib/presentation';

import { DiffRows } from './diff-view';

const DIFF = [
  '--- a/src/app.tsx',
  '+++ b/src/app.tsx',
  '@@ -1,3 +1,4 @@',
  ' const a = 1;',
  '+const b = 2;',
  ' const c = 3;',
].join('\n');

describe('DiffRows', () => {
  it('unified diff 按增删行渲染并带双侧行号', () => {
    const rows = parseUnifiedDiff(DIFF) ?? [];
    render(<DiffRows rows={rows} numbers="double" />);

    expect(screen.getByText('+const b = 2;')).toBeTruthy();
    expect(screen.getByText('--- a/src/app.tsx')).toBeTruthy();
    expect(screen.getByText(/@@ -1,3 \+1,4 @@/)).toBeTruthy();
    // 增行的新文件侧行号 2 出现在 gutter(双侧行号下会有多个匹配)
    expect(screen.getAllByText('2').length).toBeGreaterThan(0);
  });

  it('无 diff 标记时返回 null,组件收到空 rows 不渲染行', () => {
    // parseUnifiedDiff 对非 diff 文本返回 null(组件层负责回退成代码预览)
    expect(parseUnifiedDiff('plain text\nno markers')).toBeNull();
    render(<DiffRows rows={[]} />);
    expect(screen.queryByText('+')).toBeNull();
  });
});
