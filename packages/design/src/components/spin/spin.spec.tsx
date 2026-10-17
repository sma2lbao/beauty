import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Spin } from './spin.js';

describe('Spin', () => {
  it('渲染默认加载指示', () => {
    render(<Spin data-testid="spin" />);

    const spin = screen.getByTestId('spin');
    expect(spin).toHaveAttribute('data-slot', 'spin');
    expect(spin).toHaveClass('bd-spin', 'bd-spin--md');
    expect(spin).toHaveAttribute('role', 'status');
  });

  it('提供 label 时渲染说明文字', () => {
    render(<Spin label="加载中" />);

    const label = screen.getByText('加载中');
    expect(label).toHaveAttribute('data-slot', 'spin-label');
    expect(label).toHaveClass('bd-spin__label');
  });

  it('透传尺寸并支持追加 className', () => {
    render(<Spin size="lg" className="my-spin" data-testid="spin" />);

    expect(screen.getByTestId('spin')).toHaveClass(
      'bd-spin',
      'bd-spin--lg',
      'my-spin',
    );
  });

  it('渲染指示器 SVG 并对辅助技术隐藏', () => {
    const { container } = render(<Spin />);

    const svg = container.querySelector('[data-slot="spin-indicator"]');
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute('aria-hidden', 'true');
  });
});
