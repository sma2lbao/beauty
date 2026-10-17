import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Alert } from './alert.js';

describe('Alert', () => {
  it('渲染默认提示条(info / status)', () => {
    render(<Alert>这是一条说明</Alert>);

    const alert = screen.getByRole('status');
    expect(alert).toHaveAttribute('data-slot', 'alert');
    expect(alert).toHaveClass('bd-alert', 'bd-alert--info');
    expect(screen.getByText('这是一条说明')).toHaveAttribute(
      'data-slot',
      'alert-description',
    );
  });

  it('danger / warning 使用 role=alert 以便读屏立即播报', () => {
    const { rerender } = render(<Alert variant="danger">出错了</Alert>);
    expect(screen.getByRole('alert')).toHaveClass('bd-alert--danger');

    rerender(<Alert variant="warning">注意</Alert>);
    expect(screen.getByRole('alert')).toHaveClass('bd-alert--warning');
  });

  it('支持标题与正文', () => {
    render(
      <Alert variant="success" title="已保存">
        改动已写入
      </Alert>,
    );

    expect(screen.getByText('已保存')).toHaveAttribute(
      'data-slot',
      'alert-title',
    );
    expect(screen.getByText('改动已写入')).toHaveAttribute(
      'data-slot',
      'alert-description',
    );
  });

  it('显式 role 覆盖默认语义', () => {
    render(
      <Alert variant="danger" role="note">
        静态说明
      </Alert>,
    );

    expect(screen.getByRole('note')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('图标对辅助技术隐藏,支持追加 className', () => {
    const { container } = render(<Alert className="my-alert">说明</Alert>);

    expect(container.querySelector('[data-slot="alert-icon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.getByRole('status')).toHaveClass('bd-alert', 'my-alert');
  });
});
