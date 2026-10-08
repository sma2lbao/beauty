import { describe, expect, it } from 'vitest';

import { cx } from './class-name';

describe('cx', () => {
  it('拼接传入的类名', () => {
    expect(cx('bd-button', 'is-active')).toBe('bd-button is-active');
  });

  it('忽略 null、undefined、false 与空字符串', () => {
    expect(cx('a', null, undefined, false, '', 'b')).toBe('a b');
  });

  it('支持数组与嵌套数组', () => {
    expect(cx(['a', 'b'], ['c', ['d', false]], 'e')).toBe('a b c d e');
  });

  it('接受数字并转为字符串', () => {
    expect(cx('col', 4)).toBe('col 4');
  });

  it('全部为假值时返回空字符串', () => {
    expect(cx(null, false, [undefined, ''])).toBe('');
  });
});
