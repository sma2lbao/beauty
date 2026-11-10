import * as Sentry from '@sentry/react';
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * 全局渲染错误边界:任何懒加载页/组件渲染抛错时保留控制台外壳,
 * 只替换出错区域,避免整页白屏;错误同时上报 Sentry(未初始化时是 no-op)。
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    Sentry.captureException(error, {
      extra: { componentStack: info.componentStack },
    });
  }

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        role="alert"
        data-testid="error-boundary"
        className="mx-auto my-16 w-full max-w-xl rounded-xl border border-ha-danger-border bg-ha-danger-bg px-6 py-5 text-ha-text"
      >
        <h1 className="text-[16px] font-semibold">页面出错了</h1>
        <p className="mt-2 text-[13px] leading-6 text-ha-text-2">
          当前页面渲染失败，可以重新加载，或先回到工作台继续其它操作。
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-ha-border bg-ha-code px-3 py-2 font-mono text-[12px] text-ha-text-body">
          {error.message}
        </pre>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-ha-accent px-3 py-1.5 text-[13px] font-medium text-white hover:brightness-110"
          >
            重新加载
          </button>
          <a
            href="/"
            className="rounded-md border border-ha-border px-3 py-1.5 text-[13px] text-ha-text-2 hover:text-ha-text"
          >
            返回工作台
          </a>
        </div>
      </div>
    );
  }
}