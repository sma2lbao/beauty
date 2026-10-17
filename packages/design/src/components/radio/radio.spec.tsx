import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Radio, RadioGroup } from './radio.js';

describe('RadioGroup / Radio', () => {
  it('渲染单选组并默认选中 defaultValue', () => {
    render(
      <RadioGroup defaultValue="monthly" aria-label="计费周期">
        <Radio value="monthly" label="按月" />
        <Radio value="yearly" label="按年" />
      </RadioGroup>,
    );

    const group = screen.getByRole('radiogroup', { name: '计费周期' });
    expect(group).toHaveClass('bd-radio-group', 'bd-radio-group--vertical');

    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    expect(screen.getByLabelText('按月')).toBeChecked();
    expect(screen.getByLabelText('按年')).not.toBeChecked();
  });

  it('点击切换非受控选中项', () => {
    render(
      <RadioGroup defaultValue="monthly">
        <Radio value="monthly" label="按月" />
        <Radio value="yearly" label="按年" />
      </RadioGroup>,
    );

    fireEvent.click(screen.getByLabelText('按年'));
    expect(screen.getByLabelText('按年')).toBeChecked();
    expect(screen.getByLabelText('按月')).not.toBeChecked();
  });

  it('受控模式只回调不自行切换', () => {
    const handleChange = vi.fn();
    render(
      <RadioGroup value="monthly" onValueChange={handleChange}>
        <Radio value="monthly" label="按月" />
        <Radio value="yearly" label="按年" />
      </RadioGroup>,
    );

    fireEvent.click(screen.getByLabelText('按年'));
    expect(handleChange).toHaveBeenCalledWith('yearly');
    expect(screen.getByLabelText('按月')).toBeChecked();
  });

  it('组内共享同一个 name,保证原生互斥与方向键行为', () => {
    const { container } = render(
      <RadioGroup defaultValue="a">
        <Radio value="a" label="A" />
        <Radio value="b" label="B" />
      </RadioGroup>,
    );

    const names = [...container.querySelectorAll('input[type="radio"]')].map(
      (input) => input.getAttribute('name'),
    );
    expect(names[0]).toBeTruthy();
    expect(names[0]).toBe(names[1]);
  });

  it('整组禁用时所有选项不可交互', () => {
    render(
      <RadioGroup disabled defaultValue="a">
        <Radio value="a" label="A" />
        <Radio value="b" label="B" />
      </RadioGroup>,
    );

    expect(screen.getByLabelText('A')).toBeDisabled();
    expect(screen.getByLabelText('B')).toBeDisabled();
    expect(screen.getByRole('radiogroup')).toHaveAttribute(
      'data-disabled',
      'true',
    );
  });

  it('支持横向排列与追加 className', () => {
    render(
      <RadioGroup orientation="horizontal" className="my-group">
        <Radio value="a" label="A" />
      </RadioGroup>,
    );

    const group = screen.getByRole('radiogroup');
    expect(group).toHaveClass('bd-radio-group--horizontal', 'my-group');
    expect(group).toHaveAttribute('aria-orientation', 'horizontal');
  });
});
