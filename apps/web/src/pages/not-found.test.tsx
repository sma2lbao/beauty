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
  it('渲染 404 提示与回到工作台的入口', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
    expect(screen.getByRole('link', { name: '回到工作台' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.getByRole('link', { name: '任务编排' })).toHaveAttribute(
      'href',
      '/runs',
    );
  });

  it('使用内联的品牌 mark(官方几何,不再用 CSS 画的圆盘)', () => {
    renderPage();
    // 装饰性:不给可访问名,避免与正文重复播报
    expect(document.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
  });
});
