import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { App } from './app';

function renderApp(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('App 路由', () => {
  it('/ 渲染对话页', () => {
    renderApp('/');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '对话',
    );
  });

  it('/products 已下线(渲染 404)', () => {
    renderApp('/products');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('/about 渲染关于页', () => {
    renderApp('/about');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '关于 beauty moon',
    );
  });

  it('未知路由渲染 404', () => {
    renderApp('/no-such-page');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '这个页面不在清单里',
    );
  });

  it('所有路由都包在站点布局里（页头 + 页脚）', () => {
    renderApp('/');
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });
});
