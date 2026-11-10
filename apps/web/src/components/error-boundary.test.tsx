import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './error-boundary.js';

const { captureException } = vi.hoisted(() => ({
  captureException: vi.fn(),
}));

vi.mock('@sentry/react', () => ({ captureException }));

function Boom(): ReactNode {
  throw new Error('kaboom');
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    // React 会把组件错误打到 console.error;测试里只看断言。
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    captureException.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('子组件抛错时渲染兜底页并保留错误摘要', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('页面出错了')).toBeInTheDocument();
    expect(screen.getByText('kaboom')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '重新加载' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回工作台' })).toHaveAttribute(
      'href',
      '/',
    );
  });

  it('正常子组件原样渲染,不出现兜底页', () => {
    render(
      <ErrorBoundary>
        <p>一切正常</p>
      </ErrorBoundary>,
    );

    expect(screen.getByText('一切正常')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(captureException).not.toHaveBeenCalled();
  });

  it('上报异常到 Sentry', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(captureException).toHaveBeenCalledTimes(1);
    const [error] = captureException.mock.calls[0] as [Error];
    expect(error.message).toBe('kaboom');
  });
});
