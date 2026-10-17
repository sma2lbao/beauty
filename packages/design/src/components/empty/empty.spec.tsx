import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Empty } from './empty.js';

describe('Empty', () => {
  it('渲染默认空态', () => {
    render(<Empty data-testid="empty" />);

    const empty = screen.getByTestId('empty');
    expect(empty).toHaveAttribute('data-slot', 'empty');
    expect(empty).toHaveClass('bd-empty');
  });

  it('渲染描述与操作区', () => {
    render(
      <Empty
        description="暂无事件"
        actions={<button type="button">刷新</button>}
      />,
    );

    const description = screen.getByText('暂无事件');
    expect(description).toHaveAttribute('data-slot', 'empty-description');
    expect(description.tagName).toBe('P');

    const actions = screen.getByRole('button', { name: '刷新' }).parentElement;
    expect(actions).toHaveAttribute('data-slot', 'empty-actions');
    expect(actions).toHaveClass('bd-empty__actions');
  });

  it('渲染图形且对辅助技术隐藏', () => {
    const { container } = render(<Empty description="暂无数据" />);

    const figure = container.querySelector('[data-slot="empty-figure"]');
    expect(figure).not.toBeNull();
    expect(figure).toHaveAttribute('aria-hidden', 'true');
  });

  it('支持追加 className', () => {
    render(<Empty className="my-empty" data-testid="empty" />);

    expect(screen.getByTestId('empty')).toHaveClass('bd-empty', 'my-empty');
  });
});
