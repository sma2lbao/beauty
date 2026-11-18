import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Kbd } from './kbd.js';

describe('Kbd', () => {
  it('渲染 kbd 元素与语义类名', () => {
    render(<Kbd>⌘K</Kbd>);

    const kbd = screen.getByText('⌘K');
    expect(kbd.tagName).toBe('KBD');
    expect(kbd).toHaveAttribute('data-slot', 'kbd');
    expect(kbd).toHaveClass('bd-kbd');
  });

  it('透传 className 与原生属性', () => {
    render(
      <Kbd className="extra" title="执行">
        Esc
      </Kbd>,
    );

    const kbd = screen.getByText('Esc');
    expect(kbd).toHaveClass('bd-kbd', 'extra');
    expect(kbd).toHaveAttribute('title', '执行');
  });
});
