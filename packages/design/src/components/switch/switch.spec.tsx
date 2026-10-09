import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Switch } from './switch.js';

describe('Switch', () => {
  it('渲染开关并暴露 aria-checked', () => {
    render(<Switch />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(toggle).toHaveAttribute('data-state', 'unchecked');
  });

  it('非受控模式下点击切换状态', () => {
    render(<Switch defaultChecked={false} />);

    const toggle = screen.getByRole('switch');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(toggle).toHaveAttribute('data-state', 'checked');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(toggle).toHaveAttribute('data-state', 'unchecked');
  });

  it('受控模式下状态完全由 checked 决定', () => {
    const handleCheckedChange = vi.fn();
    render(
      <Switch checked={false} onCheckedChange={handleCheckedChange} />,
    );

    const toggle = screen.getByRole('switch');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(handleCheckedChange).toHaveBeenCalledWith(true);
  });

  it('配合 useState 可正常工作', () => {
    function Demo() {
      const [on, setOn] = useState(true);
      return <Switch checked={on} onCheckedChange={setOn} />;
    }
    render(<Demo />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('disabled 时不响应点击', () => {
    const handleCheckedChange = vi.fn();
    render(
      <Switch disabled defaultChecked={false} onCheckedChange={handleCheckedChange} />,
    );

    const toggle = screen.getByRole('switch');
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(handleCheckedChange).not.toHaveBeenCalled();
    expect(toggle).toHaveAttribute('data-state', 'unchecked');
  });
});
