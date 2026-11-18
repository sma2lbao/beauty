import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StatusBadge, StatusDot, StatusPill, StatusText } from './status.js';

describe('Status', () => {
  it('StatusDot 默认中性且装饰性隐藏', () => {
    const { container } = render(<StatusDot />);

    const dot = container.querySelector('[data-slot="status-dot"]');
    expect(dot).toHaveAttribute('aria-hidden', 'true');
    expect(dot).toHaveClass('bd-status-dot', 'bd-tone-dot--neutral');
  });

  it('StatusDot 支持 tone 与 pulse', () => {
    const { container } = render(<StatusDot tone="accent" pulse />);

    expect(container.querySelector('[data-slot="status-dot"]')).toHaveClass(
      'bd-tone-dot--accent',
      'bd-status-dot--pulse',
    );
  });

  it('StatusText 用 tone 上文字色', () => {
    render(<StatusText tone="danger">失败</StatusText>);

    expect(screen.getByText('失败')).toHaveClass(
      'bd-status-text',
      'bd-tone-text--danger',
    );
  });

  it('StatusBadge 默认等宽,mono=false 时用无衬线', () => {
    const { rerender } = render(<StatusBadge tone="success">OK</StatusBadge>);

    expect(screen.getByText('OK')).toHaveClass(
      'bd-status-badge',
      'bd-status-badge--mono',
      'bd-tone-soft--success',
    );

    rerender(
      <StatusBadge tone="success" mono={false}>
        OK
      </StatusBadge>,
    );
    expect(screen.getByText('OK')).not.toHaveClass('bd-status-badge--mono');
  });

  it('StatusPill 内含装饰圆点,支持两档尺寸', () => {
    const { rerender } = render(<StatusPill tone="info">运行中</StatusPill>);

    const pill = screen
      .getByText('运行中')
      .closest('[data-slot="status-pill"]');
    expect(pill).toHaveAttribute('data-size', 'sm');
    expect(pill).toHaveClass('bd-status-pill', 'bd-tone-outline--info');
    expect(pill?.querySelector('[data-slot="status-dot"]')).not.toBeNull();

    rerender(
      <StatusPill tone="info" size="md" pulse>
        运行中
      </StatusPill>,
    );
    expect(pill).toHaveAttribute('data-size', 'md');
    expect(pill).toHaveClass('bd-status-pill--md');
    expect(pill?.querySelector('.bd-status-dot--pulse')).not.toBeNull();
  });
});
