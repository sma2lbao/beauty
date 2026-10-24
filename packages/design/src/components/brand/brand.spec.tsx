import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  BrandChip,
  BrandLockup,
  BrandLogo,
  BrandMark,
  BrandTile,
  BrandWordmark,
} from './brand.js';

describe('brand', () => {
  it('渲染锁定组合:标识牌 + 产品名', () => {
    render(<BrandLockup />);

    expect(screen.getByText('beauty moon')).toBeInTheDocument();
    expect(screen.getByText('beauty moon')).toHaveClass(
      'bd-brand-lockup__text',
      'bd-brand-lockup__text--md',
    );
  });

  it('标识牌是装饰性的,不进入可访问树,尺寸跟随 size', () => {
    const { container } = render(<BrandTile size={56} />);

    const tile = container.querySelector('.bd-brand-tile');
    expect(tile).not.toBeNull();
    expect(tile).toHaveAttribute('aria-hidden', 'true');
    expect(tile).toHaveStyle({ width: '56px', height: '56px' });
    // 牌内 mark 同样装饰性,避免重复播报
    expect(tile?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });

  it('BrandMark 默认可访问,decorative 时 aria-hidden', () => {
    const { rerender } = render(<BrandMark />);

    expect(
      screen.getByRole('img', { name: 'beauty moon' }),
    ).toBeInTheDocument();

    rerender(<BrandMark decorative />);
    expect(screen.queryByRole('img', { name: 'beauty moon' })).toBeNull();
  });

  it('useId 生成的遮罩 id 唯一,重复渲染互不冲突', () => {
    const { container } = render(
      <>
        <BrandMark />
        <BrandMark />
        <BrandWordmark />
        <BrandChip />
      </>,
    );

    const ids = Array.from(container.querySelectorAll('mask')).map(
      (node) => node.id,
    );
    expect(ids.length).toBeGreaterThanOrEqual(4);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => /^bm-(moon|phase|cut)-[^:]+$/.test(id))).toBe(
      true,
    );
  });

  it('字标与图形锁定共用同一套可见文案', () => {
    const { container } = render(<BrandLogo />);

    expect(
      screen.getByRole('img', { name: 'beauty moon' }),
    ).toBeInTheDocument();
    expect(container.querySelector('.bd-brand-logo__chip')).not.toBeNull();
    expect(container.querySelector('.bd-brand-logo__wordmark')).not.toBeNull();
  });
});
