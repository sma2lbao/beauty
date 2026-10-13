import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { NotFoundPage } from './not-found';

function renderPage() {
  return render(
    <MemoryRouter>
      <NotFoundPage />
    </MemoryRouter>,
  );
}

describe('NotFoundPage', () => {
  it('渲染 404 提示与回到对话的入口', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
    expect(screen.getByRole('link', { name: '回到对话' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.getByRole('link', { name: '运行控制台' })).toHaveAttribute(
      'href',
      '/runs',
    );
  });

  it('使用官方 mark 作为品牌装饰(不再用 CSS 画的圆盘)', () => {
    renderPage();
    expect(document.querySelector('img[src="/logo-mark.svg"]')).not.toBeNull();
  });
});
