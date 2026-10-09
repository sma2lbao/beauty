import { describe, expect, it } from 'vitest';

import { cn } from './utils';

describe('cn', () => {
  it('合并多个类名', () => {
    expect(cn('flex', 'items-center')).toBe('flex items-center');
  });

  it('过滤假值', () => {
    const state = { hidden: false };
    expect(cn('flex', state.hidden && 'hidden', undefined, null, 'gap-2')).toBe(
      'flex gap-2',
    );
  });

  it('tailwind 冲突时保留后写的类', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });
});
