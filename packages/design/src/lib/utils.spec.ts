import { describe, expect, it } from 'vitest';

import { cn, cx } from './utils.js';

describe('cn', () => {
  it('拼接传入的类名', () => {
    expect(cn('inline-flex', 'items-center')).toBe('inline-flex items-center');
  });

  it('忽略 null、undefined、false 与空字符串', () => {
    expect(cn('a', null, undefined, false, '', 'b')).toBe('a b');
  });

  it('支持数组与嵌套数组', () => {
    expect(cn(['a', 'b'], ['c', ['d', false]], 'e')).toBe('a b c d e');
  });

  it('接受数字并转为字符串', () => {
    expect(cn('col', 4)).toBe('col 4');
  });

  it('不做原子类冲突合并:按传入顺序拼接(覆盖交给 CSS 层叠)', () => {
    expect(cn('bd-button', 'bd-button--primary')).toBe(
      'bd-button bd-button--primary',
    );
    expect(cn('px-3', 'px-5')).toBe('px-3 px-5');
  });

  it('全部为假值时返回空字符串', () => {
    expect(cn(null, false, [undefined, ''])).toBe('');
  });
});

describe('cx(兼容别名)', () => {
  it('拼接传入的类名', () => {
    expect(cx('bd-button', 'is-active')).toBe('bd-button is-active');
  });

  it('忽略假值并支持嵌套数组', () => {
    expect(cx(['a', 'b'], ['c', ['d', false]], 'e')).toBe('a b c d e');
    expect(cx(null, false, [undefined, ''])).toBe('');
  });
});
