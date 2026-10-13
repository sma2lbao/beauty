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
    expect(screen.getByText('美月')).toBeInTheDocument();
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
