import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Button } from './button';

describe('Button', () => {
  it('渲染按钮文本', () => {
    render(<Button>看看产品</Button>);
    expect(
      screen.getByRole('button', { name: '看看产品' }),
    ).toBeInTheDocument();
  });

  it('默认 variant 应用主色背景', () => {
    render(<Button>主按钮</Button>);
    expect(screen.getByRole('button')).toHaveClass('bg-primary');
  });

  it('variant 与 size 生效', () => {
    render(
      <Button variant="outline" size="lg">
        大描边按钮
      </Button>,
    );
    const button = screen.getByRole('button');
    expect(button).toHaveClass('border');
    expect(button).toHaveClass('h-10');
  });

  it('点击触发 onClick', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>点我</Button>);
    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('asChild 时渲染为子元素并保留按钮样式', () => {
    render(
      <Button asChild>
        <a href="/runs">链接按钮</a>
      </Button>,
    );
    const link = screen.getByRole('link', { name: '链接按钮' });
    expect(link).toHaveAttribute('href', '/runs');
    expect(link).toHaveClass('bg-primary');
  });
});
