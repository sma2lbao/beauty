import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { SiteHeader } from './site-header';

function renderHeader(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SiteHeader />
    </MemoryRouter>,
  );
}

describe('SiteHeader', () => {
  it('渲染品牌与导航链接', () => {
    renderHeader();
    // 字标按一个图形对象播报 beauty moon;可见字母里 moon 的两个 o 是月相圆盘
    expect(screen.getByRole('img', { name: 'beauty moon' })).toBeInTheDocument();
    expect(screen.getByText('beauty m')).toBeInTheDocument();
    expect(screen.getByText('n')).toBeInTheDocument();
    // 小尺寸用官方 favicon 几何的夜空底 chip(内联 SVG,非外部图片)
    expect(document.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(
      screen.getByRole('link', { name: 'beauty moon 首页' }),
    ).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: '对话' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.getByRole('link', { name: '关于' })).toHaveAttribute(
      'href',
      '/about',
    );
  });

  it('当前路由的导航项带激活下划线', () => {
    renderHeader('/runs');
    expect(screen.getByRole('link', { name: '控制台' })).toHaveClass(
      'underline',
    );
    expect(screen.getByRole('link', { name: '对话' })).not.toHaveClass(
      'underline',
    );
  });

  it('切换深浅色：更新 document 根类与 localStorage', () => {
    localStorage.removeItem('theme');
    renderHeader();

    fireEvent.click(screen.getByRole('button', { name: '切换到深色模式' }));
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('theme')).toBe('dark');

    fireEvent.click(screen.getByRole('button', { name: '切换到浅色模式' }));
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(localStorage.getItem('theme')).toBe('light');
  });
});
