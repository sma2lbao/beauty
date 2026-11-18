import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Avatar } from './avatar.js';

describe('Avatar', () => {
  it('取名字首字并大写', () => {
    render(<Avatar name="sma2lbao" />);

    const avatar = screen.getByRole('img', { name: 'sma2lbao' });
    expect(avatar).toHaveAttribute('data-slot', 'avatar');
    expect(avatar).toHaveClass('bd-avatar');
    expect(avatar).toHaveTextContent('S');
  });

  it('空名字回退为问号', () => {
    render(<Avatar name="   " />);

    const avatar = screen.getByRole('img', { name: '未知用户' });
    expect(avatar).toHaveTextContent('?');
  });

  it('size 控制边长', () => {
    render(<Avatar name="张" size={40} />);

    const avatar = screen.getByRole('img', { name: '张' });
    expect(avatar).toHaveStyle({ width: '40px', height: '40px' });
  });
});
