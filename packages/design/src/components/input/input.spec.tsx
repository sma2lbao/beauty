import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Input } from './input';

describe('Input', () => {
  it('渲染默认输入框', () => {
    render(<Input placeholder="请输入昵称" />);

    const input = screen.getByPlaceholderText('请输入昵称');
    expect(input).toBeInTheDocument();
    expect(input.closest('span')).toHaveClass('bd-input', 'bd-input--md');
  });

  it('支持受控输入与 onChange', () => {
    const handleChange = vi.fn();
    render(<Input value="美" onChange={handleChange} />);

    const input = screen.getByRole('textbox');
    expect(input).toHaveValue('美');
    fireEvent.change(input, { target: { value: '美学' } });
    expect(handleChange).toHaveBeenCalledWith(
      expect.objectContaining({ target: input }),
    );
  });

  it('invalid 时标记危险态并设置 aria-invalid', () => {
    render(<Input invalid defaultValue="" />);

    const wrapper = screen.getByRole('textbox').closest('span');
    expect(wrapper).toHaveClass('is-invalid');
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-invalid', 'true');
  });

  it('disabled 时禁用输入', () => {
    render(<Input disabled placeholder="昵称" />);

    const input = screen.getByPlaceholderText('昵称');
    expect(input).toBeDisabled();
    expect(input.closest('span')).toHaveClass('is-disabled');
  });

  it('渲染前置与后置内容', () => {
    render(<Input prefix="¥" suffix="元" placeholder="价格" />);

    expect(screen.getByText('¥')).toHaveClass('bd-input__prefix');
    expect(screen.getByText('元')).toHaveClass('bd-input__suffix');
  });

  it('透传 ref 到原生 input', () => {
    const ref = createRef<HTMLInputElement>();
    render(<Input ref={ref} placeholder="昵称" />);

    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    expect(ref.current).toBe(screen.getByPlaceholderText('昵称'));
  });
});
