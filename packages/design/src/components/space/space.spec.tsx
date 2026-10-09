import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Space } from './space.js';

describe('Space', () => {
  it('默认水平排列并应用中等间距', () => {
    render(
      <Space data-testid="space">
        <span>a</span>
        <span>b</span>
      </Space>,
    );

    const space = screen.getByTestId('space');
    expect(space).toHaveAttribute('data-slot', 'space');
    expect(space).toHaveClass('bd-space');
    expect(space).not.toHaveClass('bd-space--vertical');
    expect(space.style.gap).toBe('12px');
    expect(space).toHaveTextContent('ab');
  });

  it('支持垂直方向与自定义像素间距', () => {
    render(
      <Space direction="vertical" size={24} data-testid="space">
        <span>a</span>
      </Space>,
    );

    const space = screen.getByTestId('space');
    expect(space).toHaveClass('bd-space', 'bd-space--vertical');
    expect(space.style.gap).toBe('24px');
  });

  it('支持对齐方式与换行', () => {
    render(
      <Space align="center" wrap size="lg" data-testid="space">
        <span>a</span>
      </Space>,
    );

    const space = screen.getByTestId('space');
    expect(space).toHaveClass('bd-space--align-center', 'bd-space--wrap');
    expect(space.style.gap).toBe('16px');
  });
});
