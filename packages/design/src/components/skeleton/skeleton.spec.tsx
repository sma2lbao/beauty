import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Skeleton } from './skeleton.js';

describe('Skeleton', () => {
  it('渲染为带脉冲动画的占位块', () => {
    render(<Skeleton data-testid="skeleton" />);

    const skeleton = screen.getByTestId('skeleton');
    expect(skeleton).toHaveAttribute('data-slot', 'skeleton');
    expect(skeleton).toHaveClass('bd-skeleton');
  });

  it('对辅助技术隐藏并支持追加类名', () => {
    render(<Skeleton className="h-4 w-full" data-testid="skeleton" />);

    expect(screen.getByTestId('skeleton')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.getByTestId('skeleton')).toHaveClass(
      'bd-skeleton',
      'h-4',
      'w-full',
    );
  });
});
