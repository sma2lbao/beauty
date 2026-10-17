import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Button } from './button.js';

describe('Button', () => {
  it('渲染默认的主要按钮', () => {
    render(<Button>保存</Button>);

    const button = screen.getByRole('button', { name: '保存' });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute('data-slot', 'button');
    expect(button).toHaveClass(
      'bd-button',
      'bd-button--primary',
      'bd-button--md',
    );
    expect(button).toHaveAttribute('data-variant', 'primary');
    expect(button).toHaveAttribute('data-size', 'md');
    expect(button).toHaveAttribute('type', 'button');
  });

  it('不携带任何 Tailwind 工具类(样式由 button.css 提供)', () => {
    render(
      <Button variant="outline" size="lg" block>
        覆写
      </Button>,
    );

    const button = screen.getByRole('button');
    const classes = button.className.split(/\s+/);
    expect(classes.every((name) => name.startsWith('bd-'))).toBe(true);
    expect(button).toHaveClass(
      'bd-button--outline',
      'bd-button--lg',
      'bd-button--block',
    );
  });

  it('支持 variant 与 size', () => {
    render(
      <Button variant="outline" size="lg">
        外部按钮
      </Button>,
    );

    expect(screen.getByRole('button')).toHaveClass(
      'bd-button--outline',
      'bd-button--lg',
    );
  });

  it('block 时撑满宽度', () => {
    render(<Button block>提交</Button>);

    expect(screen.getByRole('button')).toHaveClass('bd-button--block');
  });

  it('透传 onClick 并响应点击', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>确定</Button>);

    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('disabled 时不可交互', () => {
    const handleClick = vi.fn();
    render(
      <Button disabled onClick={handleClick}>
        确定
      </Button>,
    );

    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it('loading 时禁用交互并进入忙碌状态', () => {
    const handleClick = vi.fn();
    render(
      <Button loading onClick={handleClick}>
        保存
      </Button>,
    );

    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(button.querySelector('[data-slot="button-spinner"]')).not.toBeNull();
    fireEvent.click(button);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it('icon 尺寸渲染方形按钮', () => {
    render(
      <Button size="icon" aria-label="切换主题">
        <svg viewBox="0 0 16 16" />
      </Button>,
    );

    const button = screen.getByRole('button', { name: '切换主题' });
    expect(button).toHaveClass('bd-button', 'bd-button--icon');
    expect(button).toHaveAttribute('data-size', 'icon');
  });

  it('asChild 把样式与 props 合并到子元素上', () => {
    const handleClick = vi.fn();
    render(
      <Button asChild onClick={handleClick}>
        <a href="/runs">链接按钮</a>
      </Button>,
    );

    const link = screen.getByRole('link', { name: '链接按钮' });
    expect(link).toHaveAttribute('href', '/runs');
    expect(link).toHaveClass('bd-button', 'bd-button--primary');
    expect(link).toHaveAttribute('data-slot', 'button');

    fireEvent.click(link);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('asChild 禁用时标记 aria-disabled 并拦截跳转', () => {
    render(
      <Button asChild disabled>
        <a href="/runs">链接按钮</a>
      </Button>,
    );

    const link = screen.getByRole('link', { name: '链接按钮' });
    expect(link).toHaveAttribute('aria-disabled', 'true');

    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('asChild 加载态把加载指示放进子元素', () => {
    const { container } = render(
      <Button asChild loading>
        <a href="/runs">链接按钮</a>
      </Button>,
    );

    const spinner = container.querySelector('[data-slot="button-spinner"]');
    expect(spinner).not.toBeNull();
    expect(spinner?.parentElement?.tagName).toBe('A');
  });

  it('透传其余原生属性与 className', () => {
    render(
      <Button data-testid="cta" className="custom" form="login">
        登录
      </Button>,
    );

    const button = screen.getByTestId('cta');
    expect(button).toHaveAttribute('form', 'login');
    expect(button).toHaveClass('custom');
  });
});
