import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Input } from './input.js';

describe('Input', () => {
  it('渲染默认输入框', () => {
    render(<Input placeholder="请输入昵称" />);

    const input = screen.getByPlaceholderText('请输入昵称');
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute('data-slot', 'input');
    const wrapper = input.closest('span');
    expect(wrapper).toHaveAttribute('data-slot', 'input-group');
    expect(wrapper).toHaveClass('bd-input-group', 'bd-input-group--md');
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
    expect(wrapper).toHaveAttribute('data-invalid', 'true');
    const classes = wrapper?.className.split(/\s+/) ?? [];
    expect(classes.length).toBeGreaterThan(0);
    expect(classes.every((name) => name.startsWith('bd-'))).toBe(true);
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-invalid', 'true');
  });

  it('disabled 时禁用输入', () => {
    render(<Input disabled placeholder="昵称" />);

    const input = screen.getByPlaceholderText('昵称');
    expect(input).toBeDisabled();
    const wrapper = input.closest('span');
    expect(wrapper).toHaveAttribute('data-disabled', 'true');
  });

  it('渲染前置与后置内容', () => {
    render(<Input prefix="¥" suffix="元" placeholder="价格" />);

    expect(screen.getByText('¥')).toHaveAttribute('data-slot', 'input-prefix');
    expect(screen.getByText('元')).toHaveAttribute('data-slot', 'input-suffix');
  });

  it('透传 ref 到原生 input', () => {
    const ref = createRef<HTMLInputElement>();
    render(<Input ref={ref} placeholder="昵称" />);

    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    expect(ref.current).toBe(screen.getByPlaceholderText('昵称'));
  });
});
