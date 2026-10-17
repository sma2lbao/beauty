import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Tabs } from './tabs.js';

const items = [
  { value: 'login', label: '登录', content: <p>登录表单</p> },
  { value: 'register', label: '注册', content: <p>注册表单</p> },
];

describe('Tabs', () => {
  it('默认选中第一项并渲染对应面板', () => {
    render(<Tabs items={items} />);

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('登录表单');
  });

  it('点击切换页签与面板(非受控)', () => {
    render(<Tabs items={items} />);

    fireEvent.click(screen.getByRole('tab', { name: '注册' }));

    expect(screen.getByRole('tab', { name: '注册' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tabpanel')).toHaveTextContent('注册表单');
  });

  it('受控模式只回调不自行切换', () => {
    const handleChange = vi.fn();
    render(<Tabs items={items} value="login" onValueChange={handleChange} />);

    fireEvent.click(screen.getByRole('tab', { name: '注册' }));
    expect(handleChange).toHaveBeenCalledWith('register');
    expect(screen.getByRole('tab', { name: '登录' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('键盘 → 在页签间移动选中', () => {
    render(<Tabs items={items} />);

    const login = screen.getByRole('tab', { name: '登录' });
    fireEvent.keyDown(login, { key: 'ArrowRight' });

    expect(screen.getByRole('tab', { name: '注册' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    fireEvent.keyDown(screen.getByRole('tab', { name: '注册' }), {
      key: 'ArrowLeft',
    });
    expect(screen.getByRole('tab', { name: '登录' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('block 时页签条撑满容器', () => {
    render(<Tabs block items={items} />);

    expect(screen.getByRole('tablist').parentElement).toHaveClass(
      'bd-tabs--block',
    );
  });

  it('不传 content 时只渲染页签条', () => {
    render(
      <Tabs
        items={[
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B' },
        ]}
      />,
    );

    expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(2);
  });

  it('禁用项不可点击,支持追加 className', () => {
    const handleChange = vi.fn();
    render(
      <Tabs
        items={[
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B', disabled: true },
        ]}
        onValueChange={handleChange}
        className="my-tabs"
      />,
    );

    expect(screen.getByRole('tab', { name: 'B' })).toBeDisabled();
    fireEvent.click(screen.getByRole('tab', { name: 'B' }));
    expect(handleChange).not.toHaveBeenCalled();
    expect(screen.getByRole('tablist').parentElement).toHaveClass(
      'bd-tabs',
      'my-tabs',
    );
  });
});
