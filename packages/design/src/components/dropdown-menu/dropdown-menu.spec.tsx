import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './dropdown-menu.js';

function BasicMenu({ children }: { children?: ReactNode }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger>更多</DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>操作</DropdownMenuLabel>
        <DropdownMenuItem>重命名</DropdownMenuItem>
        <DropdownMenuItem>删除</DropdownMenuItem>
        <DropdownMenuSeparator />
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

describe('DropdownMenu', () => {
  it('默认收起,面板不渲染', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('点击触发器展开,再点收起', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger).toHaveAttribute('aria-controls');
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: '重命名' }),
    ).toBeInTheDocument();

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('点击菜单项触发 onSelect 并自动收起', () => {
    const handleSelect = vi.fn();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={handleSelect}>重命名</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));

    expect(handleSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('onSelect 阻止默认行为时保持展开', () => {
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            onSelect={(event) => {
              event.preventDefault();
            }}
          >
            不收起
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '不收起' }));

    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('Escape 收起并把焦点还给触发器', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('点击面板外部收起', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    fireEvent.click(trigger);
    expect(screen.getByRole('menu')).toBeInTheDocument();

    fireEvent.mouseDown(document.body);

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('面板内点击不收起(Escape 之外的空白处)', () => {
    render(<BasicMenu />);

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    fireEvent.mouseDown(screen.getByRole('menu'));
    fireEvent.click(screen.getByRole('menu'));

    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('方向键打开并聚焦首项,方向键在项间循环', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });

    const first = screen.getByRole('menuitem', { name: '重命名' });
    const second = screen.getByRole('menuitem', { name: '删除' });
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(second).toHaveFocus();

    fireEvent.keyDown(second, { key: 'ArrowDown' });
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: 'ArrowUp' });
    expect(second).toHaveFocus();

    fireEvent.keyDown(second, { key: 'Home' });
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: 'End' });
    expect(second).toHaveFocus();
  });

  it('ArrowUp 打开时聚焦最后一项', () => {
    render(<BasicMenu />);

    fireEvent.keyDown(screen.getByRole('button', { name: '更多' }), {
      key: 'ArrowUp',
    });

    expect(screen.getByRole('menuitem', { name: '删除' })).toHaveFocus();
  });

  it('Tab 离开时收起', () => {
    render(<BasicMenu />);

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' });

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('单选菜单项汇报选中态,点击后收起', () => {
    const handleSelect = vi.fn();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>切换工作区</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuRadioGroup>
            <DropdownMenuRadioItem checked onSelect={handleSelect}>
              工作区 A
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem>工作区 B</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    fireEvent.click(screen.getByRole('button', { name: '切换工作区' }));

    expect(
      screen.getByRole('menuitemradio', { name: '工作区 A' }),
    ).toHaveAttribute('aria-checked', 'true');
    expect(
      screen.getByRole('menuitemradio', { name: '工作区 B' }),
    ).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(screen.getByRole('menuitemradio', { name: '工作区 B' }));
    expect(handleSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('受控模式只回调,不自行展开', () => {
    const handleOpenChange = vi.fn();
    render(
      <DropdownMenu open={false} onOpenChange={handleOpenChange}>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>重命名</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    fireEvent.click(screen.getByRole('button', { name: '更多' }));

    expect(handleOpenChange).toHaveBeenCalledWith(true);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('受控 open 时直接渲染面板', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent size="sm">
          <DropdownMenuItem>重命名</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(screen.getByRole('menuitem')).toHaveClass(
      'bd-dropdown-menu__item',
      'bd-dropdown-menu__item--sm',
    );
  });

  it('asChild 触发器复用子元素外观并保留链接语义', () => {
    render(
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <a href="/runs" onClick={(event) => event.preventDefault()}>
            运行
          </a>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>重命名</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    const link = screen.getByRole('link', { name: '运行' });
    expect(link).toHaveAttribute('href', '/runs');
    expect(link).not.toHaveClass('bd-dropdown-menu__trigger');
    expect(link).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(link);
    expect(link).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('asChild 菜单项保留 href 并自动收起', () => {
    render(
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger>更多</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem asChild>
            <a href="/changes" onClick={(event) => event.preventDefault()}>
              查看变更
            </a>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    const item = screen.getByRole('menuitem', { name: '查看变更' });
    expect(item).toHaveAttribute('href', '/changes');
    expect(item).toHaveClass('bd-dropdown-menu__item');

    fireEvent.click(item);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('关闭后焦点被选中项卸载时回到触发器', () => {
    render(<BasicMenu />);

    const trigger = screen.getByRole('button', { name: '更多' });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));

    expect(trigger).toHaveFocus();
  });
});
