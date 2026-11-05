/**
 * 「更多」入口的行为:收起态、弹层内容、跳转与关闭方式。
 *
 * 这里单独渲染 MoreNav(不拉整个侧栏):侧栏用例要 mock 会话/工作区/账户,
 * 而弹层行为只跟路由与 DOM 有关 —— 分开测更快,失败信息也更指向问题。
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';

import { MoreNav } from './more-nav';

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{location.pathname}</span>;
}

function renderMoreNav(entry = '/') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <MoreNav />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function trigger() {
  return screen.getByRole('button', { name: '更多' });
}

describe('MoreNav 更多入口', () => {
  it('默认收起,展开后才出现资源与管理两组入口', () => {
    renderMoreNav();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('link', { name: '知识库' })).toBeNull();

    fireEvent.click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute(
      'href',
      '/knowledge',
    );
    expect(screen.getByRole('link', { name: '事件队列' })).toHaveAttribute(
      'href',
      '/outbox',
    );
    // 分组标签沿用 nav-data 的 label,方便用户按「资源/管理」扫视
    expect(screen.getByText('资源')).toBeInTheDocument();
    expect(screen.getByText('管理')).toBeInTheDocument();
  });

  it('当前页在弹层里时,收起态也能看出「我在哪」', () => {
    renderMoreNav('/memory');
    expect(trigger()).toHaveAttribute('data-active', 'true');
    expect(within(trigger()).getByText('更多')).toBeInTheDocument();
  });

  it('选中一项后跳转,弹层不跟到新页面', () => {
    renderMoreNav();
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('link', { name: '知识库' }));

    expect(screen.getByTestId('location')).toHaveTextContent('/knowledge');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('Escape 收起,焦点回到触发器', () => {
    renderMoreNav();
    fireEvent.click(trigger());
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(trigger()).toHaveFocus();
  });

  it('点外部区域收起', () => {
    renderMoreNav();
    fireEvent.click(trigger());
    fireEvent.mouseDown(document.body);

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });
});
