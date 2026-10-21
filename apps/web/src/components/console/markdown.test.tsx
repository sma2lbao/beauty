import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Markdown } from './markdown';

describe('Markdown', () => {
  it('渲染标题、列表、表格与链接', () => {
    render(
      <Markdown
        content={[
          '# 周报',
          '',
          '- 日程已汇总',
          '',
          '| 项目 | 状态 |',
          '| --- | --- |',
          '| 周报 | 完成 |',
          '',
          '[文档](https://example.com)',
        ].join('\n')}
      />,
    );

    expect(screen.getByRole('heading', { name: '周报' })).toBeInTheDocument();
    expect(screen.getByText('日程已汇总')).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('完成')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: '文档' });
    expect(link).toHaveAttribute('href', 'https://example.com');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('清洗脚本与 javascript 协议(不渲染 raw HTML)', () => {
    const { container } = render(
      <Markdown
        content={'<script>alert(1)</script>\n\n[危险](javascript:alert(1))'}
      />,
    );

    expect(container.querySelector('script')).toBeNull();
    expect(container.innerHTML).not.toContain('javascript:alert');
  });

  it('渲染代码块,流式空内容显示占位', () => {
    const { container, rerender } = render(
      <Markdown
        content={'\u0060\u0060\u0060ts\nconst a = 1;\n\u0060\u0060\u0060'}
      />,
    );
    expect(container.querySelector('pre')).not.toBeNull();
    expect(container.textContent).toContain('const a = 1;');

    rerender(<Markdown content="" streaming />);
    expect(screen.getByText('正在回答…')).toBeInTheDocument();
  });
});
