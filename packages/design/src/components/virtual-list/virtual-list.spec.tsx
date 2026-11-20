import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { VirtualList } from './virtual-list.js';

const ITEMS = Array.from({ length: 1000 }, (_, index) => '会话 ' + index);

function renderedIndexes(container: HTMLElement) {
  return Array.from(
    container.querySelectorAll('[data-slot="virtual-list-item"]'),
  ).map((element) => Number(element.getAttribute('data-index')));
}

function scroller(container: HTMLElement) {
  return container.querySelector('[data-slot="virtual-list"]') as HTMLElement;
}

describe('VirtualList', () => {
  it('只渲染视口附近的项,并把总高度撑给滚动条', () => {
    const { container } = render(
      <VirtualList
        items={ITEMS}
        itemHeight={40}
        renderItem={(item) => <span>{item}</span>}
      />,
    );

    const indexes = renderedIndexes(container);
    expect(indexes[0]).toBe(0);
    expect(indexes.length).toBeGreaterThan(0);
    expect(indexes.length).toBeLessThan(30);
    expect(screen.getByText('会话 0')).toBeInTheDocument();
    expect(screen.queryByText('会话 999')).toBeNull();

    const spacer = container.querySelector(
      '[data-slot="virtual-list-spacer"]',
    ) as HTMLElement;
    expect(spacer.style.height).toBe('40000px');
  });

  it('滚动后窗口跟着移动,离开视口的项被卸载', () => {
    const { container } = render(
      <VirtualList
        items={ITEMS}
        itemHeight={40}
        overscan={0}
        renderItem={(item) => <span>{item}</span>}
      />,
    );

    const node = scroller(container);
    node.scrollTop = 4000;
    fireEvent.scroll(node);

    const indexes = renderedIndexes(container);
    expect(indexes).toContain(100);
    expect(indexes).not.toContain(0);
    expect(screen.getByText('会话 100')).toBeInTheDocument();
    expect(screen.queryByText('会话 0')).toBeNull();
  });

  it('变高行按前缀和定位', () => {
    const heights = [20, 60, 40, 100];
    const { container } = render(
      <VirtualList
        items={heights}
        itemHeight={(height) => height}
        renderItem={(height, index) => <span>{index + ':' + height}</span>}
      />,
    );

    const items = Array.from(
      container.querySelectorAll('[data-slot="virtual-list-item"]'),
    ) as HTMLElement[];
    expect(items.map((item) => item.style.height)).toEqual([
      '20px',
      '60px',
      '40px',
      '100px',
    ]);
    expect(items.map((item) => item.style.transform)).toEqual([
      'translateY(0px)',
      'translateY(20px)',
      'translateY(80px)',
      'translateY(120px)',
    ]);
  });

  it('空数据只保留占位层,并把滚动事件透传给调用方', () => {
    const onScroll = vi.fn();
    const { container } = render(
      <VirtualList
        items={[]}
        itemHeight={40}
        onScroll={onScroll}
        renderItem={(item) => <span>{item}</span>}
      />,
    );

    expect(renderedIndexes(container)).toEqual([]);
    const spacer = container.querySelector(
      '[data-slot="virtual-list-spacer"]',
    ) as HTMLElement;
    expect(spacer.style.height).toBe('0px');

    fireEvent.scroll(scroller(container));
    expect(onScroll).toHaveBeenCalledTimes(1);
  });
});
