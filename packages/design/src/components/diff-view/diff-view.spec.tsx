import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CodePreview, DiffRows, DiffViewer } from './diff-view.js';
import { parseUnifiedDiff } from './diff.js';

const diff = [
  '@@ -1,2 +1,2 @@',
  '-const a = 1;',
  '+const a = 2;',
  ' export const b = 3;',
].join('\n');

describe('DiffRows', () => {
  it('按行类型挂语义类,双列行号显示前后两列', () => {
    const { container } = render(
      <DiffRows rows={parseUnifiedDiff(diff) ?? []} numbers="double" />,
    );
    expect(container.querySelectorAll('.bd-diff__row--add')).toHaveLength(1);
    expect(container.querySelectorAll('.bd-diff__row--del')).toHaveLength(1);
    expect(container.querySelectorAll('.bd-diff__row--hunk')).toHaveLength(1);
    expect(container.querySelectorAll('.bd-diff__gutter')).toHaveLength(8);
  });
});

describe('DiffViewer', () => {
  it('识别 unified diff 渲染增删行', () => {
    const { container } = render(<DiffViewer content={diff} />);
    expect(container.querySelector('[data-slot="diff-rows"]')).not.toBeNull();
    expect(screen.getByText('+const a = 2;')).toBeInTheDocument();
  });

  it('不是 diff 时回退成代码预览,行号跟随 numbers', () => {
    const { container, rerender } = render(
      <DiffViewer content={'export const a = 1;'} />,
    );
    const preview = container.querySelector('[data-slot="code-preview"]');
    expect(preview).not.toBeNull();
    // 默认 numbers="none":消息流里不占横向空间
    expect(container.querySelectorAll('.bd-diff__gutter')).toHaveLength(0);
    expect(container.textContent).toContain('export const a = 1;');

    rerender(<DiffViewer content={'export const a = 1;'} numbers="single" />);
    expect(container.querySelector('.bd-diff__gutter')?.textContent).toBe('1');
  });

  it('split 视图左右两栏各渲染一份行', () => {
    const { container } = render(<DiffViewer content={diff} view="split" />);
    expect(container.querySelector('[data-slot="diff-split"]')).not.toBeNull();
    expect(container.querySelectorAll('.bd-diff__side')).toHaveLength(2);
    expect(container.querySelectorAll('.bd-diff__row--add')).toHaveLength(1);
    expect(container.querySelectorAll('.bd-diff__row--del')).toHaveLength(1);
  });
});

describe('CodePreview', () => {
  it('numbers=false 时不渲染行号', () => {
    const { container } = render(
      <CodePreview content={'a\nb'} numbers={false} />,
    );
    expect(container.querySelectorAll('.bd-diff__gutter')).toHaveLength(0);
    expect(container.querySelectorAll('.bd-code__row')).toHaveLength(2);
  });
});
