import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Textarea } from './textarea.js';

describe('Textarea', () => {
  it('渲染默认多行文本框', () => {
    render(<Textarea placeholder="备注" />);

    const textarea = screen.getByPlaceholderText('备注');
    expect(textarea).toHaveAttribute('data-slot', 'textarea');
    expect(textarea).toHaveClass('bd-textarea');
  });

  it('支持校验失败态', () => {
    render(<Textarea invalid placeholder="备注" />);

    expect(screen.getByPlaceholderText('备注')).toHaveAttribute(
      'data-invalid',
      'true',
    );
    expect(screen.getByPlaceholderText('备注')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
  });

  it('禁用态标记 data-disabled 并透传 rows', () => {
    render(<Textarea disabled rows={5} placeholder="备注" />);

    const textarea = screen.getByPlaceholderText('备注');
    expect(textarea).toHaveAttribute('data-disabled', 'true');
    expect(textarea).toHaveAttribute('rows', '5');
    expect(textarea).toBeDisabled();
  });

  it('支持追加 className', () => {
    render(<Textarea className="my-area" placeholder="备注" />);

    expect(screen.getByPlaceholderText('备注')).toHaveClass(
      'bd-textarea',
      'my-area',
    );
  });
});
