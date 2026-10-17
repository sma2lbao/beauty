import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Select } from './select.js';

describe('Select', () => {
  it('渲染原生 select 并带语义类名', () => {
    render(
      <Select defaultValue="a">
        <option value="">请选择</option>
        <option value="a">选项 A</option>
      </Select>,
    );

    const control = screen.getByRole('combobox');
    expect(control).toHaveAttribute('data-slot', 'select-control');
    expect(control.parentElement).toHaveClass('bd-select', 'bd-select--md');
  });

  it('透传尺寸与校验态', () => {
    render(
      <Select size="sm" invalid aria-label="选择">
        <option value="">请选择</option>
      </Select>,
    );

    const wrapper = screen.getByRole('combobox').parentElement;
    expect(wrapper).toHaveClass('bd-select--sm');
    expect(wrapper).toHaveAttribute('data-invalid', 'true');
    expect(screen.getByRole('combobox')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
  });

  it('onChange 正常透传', () => {
    const handleChange = vi.fn();
    render(
      <Select onChange={handleChange} aria-label="选择">
        <option value="a">A</option>
        <option value="b">B</option>
      </Select>,
    );

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'b' } });
    expect(handleChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('combobox')).toHaveValue('b');
  });

  it('禁用态标记 data-disabled', () => {
    render(
      <Select disabled aria-label="选择">
        <option value="">请选择</option>
      </Select>,
    );

    const control = screen.getByRole('combobox');
    expect(control).toBeDisabled();
    expect(control.parentElement).toHaveAttribute('data-disabled', 'true');
  });

  it('支持追加 className', () => {
    render(
      <Select className="my-select" aria-label="选择">
        <option value="">请选择</option>
      </Select>,
    );

    expect(screen.getByRole('combobox').parentElement).toHaveClass(
      'bd-select',
      'my-select',
    );
  });
});
