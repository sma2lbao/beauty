import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { SiteHeader } from './site-header';

function renderHeader(initialEntry = '/about') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SiteHeader />
    </MemoryRouter>,
  );
}

describe('SiteHeader', () => {
  it('渲染品牌与公开导航', () => {
    renderHeader();

    expect(
      screen.getByRole('link', { name: 'beauty moon 官网首页' }),
    ).toHaveAttribute('href', '/landing');
    expect(screen.getByRole('link', { name: '产品官网' })).toHaveAttribute(
      'href',
      '/landing',
    );
    expect(screen.getByRole('link', { name: '关于' })).toHaveAttribute(
      'href',
      '/about',
    );
    expect(screen.getByRole('link', { name: '控制台' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.getByRole('link', { name: '登录' })).toHaveAttribute(
      'href',
      '/login',
    );
    // 设计稿是纯暗色控制台,不再提供深浅色切换
    expect(
      screen.queryByRole('button', { name: /深色模式|浅色模式/ }),
    ).toBeNull();
  });

  it('当前路由的导航项带激活底色', () => {
    renderHeader('/about');
    expect(screen.getByRole('link', { name: '关于' })).toHaveClass(
      'bg-[#1c1d1f]',
    );
    expect(screen.getByRole('link', { name: '产品官网' })).not.toHaveClass(
      'bg-[#1c1d1f]',
    );
  });
});
