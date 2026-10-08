import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Tag } from './tag';

describe('Tag', () => {
  it('渲染默认标签', () => {
    render(<Tag>新品</Tag>);

    const tag = screen.getByText('新品');
    expect(tag).toHaveClass('bd-tag');
    expect(tag).not.toHaveClass('bd-tag--primary');
  });

  it('支持语义颜色', () => {
    render(<Tag color="success">已上架</Tag>);

    expect(screen.getByText('已上架')).toHaveClass('bd-tag--success');
  });

  it('closable 时渲染关闭按钮并触发 onClose', () => {
    const handleClose = vi.fn();
    render(
      <Tag closable onClose={handleClose}>
        热门
      </Tag>,
    );

    const close = screen.getByRole('button', { name: '移除' });
    expect(close).toHaveClass('bd-tag__close');
    fireEvent.click(close);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('未设置 closable 时不渲染关闭按钮', () => {
    render(<Tag>普通</Tag>);

    expect(
      screen.queryByRole('button', { name: '移除' }),
    ).not.toBeInTheDocument();
  });
});
