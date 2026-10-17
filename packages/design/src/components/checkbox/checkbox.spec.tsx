import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Checkbox } from './checkbox.js';

describe('Checkbox', () => {
  it('渲染默认复选框与说明文字', () => {
    render(<Checkbox label="接收通知" />);

    const input = screen.getByRole('checkbox', { name: '接收通知' });
    expect(input).toHaveAttribute('data-slot', 'checkbox');
    expect(input).toHaveAttribute('data-state', 'unchecked');
    expect(screen.getByText('接收通知')).toHaveAttribute(
      'data-slot',
      'checkbox-label',
    );
  });

  it('非受控点击切换选中态', () => {
    render(<Checkbox label="接收通知" />);

    const input = screen.getByRole('checkbox');
    expect(input).toHaveAttribute('data-state', 'unchecked');
    fireEvent.click(input);
    expect(input).toHaveAttribute('data-state', 'checked');
  });

  it('onCheckedChange 在受控与非受控模式下均触发', () => {
    const handleChange = vi.fn();
    const { rerender } = render(
      <Checkbox
        checked={false}
        onCheckedChange={handleChange}
        label="接收通知"
      />,
    );

    fireEvent.click(screen.getByRole('checkbox'));
    expect(handleChange).toHaveBeenCalledWith(true);

    rerender(
      <Checkbox checked onCheckedChange={handleChange} label="接收通知" />,
    );
    expect(screen.getByRole('checkbox')).toHaveAttribute(
      'data-state',
      'checked',
    );
  });

  it('禁用态不可交互', () => {
    render(<Checkbox disabled label="接收通知" />);

    const input = screen.getByRole('checkbox');
    expect(input).toBeDisabled();
    expect(input.parentElement).toHaveAttribute('data-disabled', 'true');
  });

  it('无 label 时不渲染说明文字', () => {
    const { container } = render(<Checkbox />);

    expect(container.querySelector('[data-slot="checkbox-label"]')).toBeNull();
  });
});
