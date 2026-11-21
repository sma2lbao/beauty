import { fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './resizable.js';

function setRect(element: Element, width: number, height: number) {
  Object.defineProperty(element, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({
      width,
      height,
      top: 0,
      left: 0,
      right: width,
      bottom: height,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }),
  });
}

function drag(
  handle: HTMLElement,
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  fireEvent.pointerDown(handle, {
    button: 0,
    pointerId: 1,
    clientX: from.x,
    clientY: from.y,
  });
  fireEvent(
    window,
    new PointerEvent('pointermove', {
      bubbles: true,
      pointerId: 1,
      clientX: to.x,
      clientY: to.y,
    }),
  );
  fireEvent.pointerUp(window, { pointerId: 1 });
}

describe('Resizable', () => {
  it('按默认尺寸渲染两个面板与分隔条', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const panels = container.querySelectorAll('[data-slot="resizable-panel"]');
    expect(panels).toHaveLength(2);
    expect(panels[0]).toHaveAttribute('data-size', '30');
    expect(panels[1]).toHaveAttribute('data-size', '70');

    const handle = screen.getByRole('separator');
    expect(handle).toHaveAttribute('aria-orientation', 'vertical');
    expect(handle).toHaveAttribute('aria-valuenow', '30');
    expect(handle).toHaveAttribute('aria-valuetext', '30%');
    expect(handle).toHaveAttribute('tabindex', '0');
  });

  it('StrictMode 下重复注册不会打乱面板顺序与尺寸', () => {
    const { container } = render(
      <StrictMode>
        <ResizablePanelGroup>
          <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
          <ResizableHandle />
          <ResizablePanel>右侧</ResizablePanel>
        </ResizablePanelGroup>
      </StrictMode>,
    );

    const panels = [
      ...container.querySelectorAll('[data-slot="resizable-panel"]'),
    ];
    expect(panels.map((panel) => panel.getAttribute('data-size'))).toEqual([
      '30',
      '70',
    ]);
    expect(screen.getByRole('separator')).toHaveAttribute(
      'aria-valuenow',
      '30',
    );
  });

  it('只给部分面板默认尺寸时,其余面板吃掉剩余空间', () => {
    const { container } = render(
      <ResizablePanelGroup direction="vertical">
        <ResizablePanel defaultSize={60}>上</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>下</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const sizes = [
      ...container.querySelectorAll('[data-slot="resizable-panel"]'),
    ].map((panel) => Number(panel.getAttribute('data-size')));
    expect(sizes).toEqual([60, 40]);
  });

  it('没有默认尺寸时均分,分隔条报告前一个面板占比', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel>A</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>B</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>C</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const sizes = [
      ...container.querySelectorAll('[data-slot="resizable-panel"]'),
    ].map((panel) => Number(panel.getAttribute('data-size')));
    expect(sizes[0]).toBeCloseTo(100 / 3, 1);
    expect(sizes[2]).toBeCloseTo(100 / 3, 1);

    expect(screen.getAllByRole('separator')[0]).toHaveAttribute(
      'aria-valuenow',
      '33',
    );
  });

  it('方向键调整 10%,Shift 微调 1%,并触发 onLayout', () => {
    const onLayout = vi.fn();
    render(
      <ResizablePanelGroup onLayout={onLayout}>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const handle = screen.getByRole('separator');
    expect(onLayout).not.toHaveBeenCalled();

    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle).toHaveAttribute('aria-valuenow', '40');
    expect(onLayout).toHaveBeenLastCalledWith([40, 60]);

    fireEvent.keyDown(handle, { key: 'ArrowRight', shiftKey: true });
    expect(handle).toHaveAttribute('aria-valuenow', '41');
    expect(onLayout).toHaveBeenLastCalledWith([41, 59]);

    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(handle).toHaveAttribute('aria-valuenow', '31');
  });

  it('垂直分栏用上下方向键,分隔条 aria-orientation 为 horizontal', () => {
    const { container } = render(
      <ResizablePanelGroup direction="vertical">
        <ResizablePanel defaultSize={30}>上</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>下</ResizablePanel>
      </ResizablePanelGroup>,
    );

    expect(
      container.querySelector('[data-slot="resizable-panel-group"]'),
    ).toHaveAttribute('data-direction', 'vertical');

    const handle = screen.getByRole('separator');
    expect(handle).toHaveAttribute('aria-orientation', 'horizontal');

    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    expect(handle).toHaveAttribute('aria-valuenow', '40');

    fireEvent.keyDown(handle, { key: 'ArrowUp', shiftKey: true });
    expect(handle).toHaveAttribute('aria-valuenow', '39');
  });

  it('指针拖拽按像素换算百分比', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const panels = container.querySelectorAll<HTMLElement>(
      '[data-slot="resizable-panel"]',
    );
    setRect(panels[0], 300, 200);
    setRect(panels[1], 700, 200);

    const handle = screen.getByRole('separator');
    drag(handle, { x: 100, y: 20 }, { x: 150, y: 20 });

    // 可分配宽度 1000px,向右 50px = +5%
    expect(handle).toHaveAttribute('aria-valuenow', '35');
    expect(panels[0]).toHaveAttribute('data-size', '35');
    expect(panels[1]).toHaveAttribute('data-size', '65');
  });

  it('拖拽遵守 minSize / maxSize', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30} minSize={25} maxSize={40}>
          左侧
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const panels = container.querySelectorAll<HTMLElement>(
      '[data-slot="resizable-panel"]',
    );
    setRect(panels[0], 300, 200);
    setRect(panels[1], 700, 200);

    const handle = screen.getByRole('separator');

    drag(handle, { x: 100, y: 20 }, { x: 600, y: 20 });
    expect(handle).toHaveAttribute('aria-valuenow', '40');

    drag(handle, { x: 100, y: 20 }, { x: -400, y: 20 });
    expect(handle).toHaveAttribute('aria-valuenow', '25');
  });

  it('双击分隔条恢复初始布局', () => {
    render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const handle = screen.getByRole('separator');
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle).toHaveAttribute('aria-valuenow', '40');

    fireEvent.doubleClick(handle);
    expect(handle).toHaveAttribute('aria-valuenow', '30');
  });

  it('withHandle 渲染可见手柄,禁用时不可聚焦也不响应键盘', () => {
    render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const handle = screen.getByRole('separator');
    expect(handle.querySelector('.bd-resizable__grip')).not.toBeNull();
    expect(handle).toHaveAttribute('data-with-handle', 'true');
  });

  it('disabled 时分隔条不可聚焦', () => {
    render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>左侧</ResizablePanel>
        <ResizableHandle disabled />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const handle = screen.getByRole('separator');
    expect(handle).toHaveAttribute('tabindex', '-1');
    expect(handle).toHaveAttribute('aria-disabled', 'true');

    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle).toHaveAttribute('aria-valuenow', '30');
  });

  it('嵌套分组下外层拖拽只按外层面板计算可分配空间', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>
          <ResizablePanelGroup direction="vertical">
            <ResizablePanel defaultSize={40}>上</ResizablePanel>
            <ResizableHandle />
            <ResizablePanel>下</ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const outerGroup = container.querySelector<HTMLElement>(
      '[data-slot="resizable-panel-group"]',
    )!;
    const outerPanels = [...outerGroup.children].filter(
      (element): element is HTMLElement =>
        element instanceof HTMLElement &&
        element.hasAttribute('data-resizable-panel-id'),
    );
    setRect(outerPanels[0], 300, 200);
    setRect(outerPanels[1], 700, 200);

    // 内层面板也占宽度,但不能计入外层可分配空间
    const innerPanels = outerPanels[0].querySelectorAll<HTMLElement>(
      '[data-slot="resizable-panel"]',
    );
    setRect(innerPanels[0], 300, 100);
    setRect(innerPanels[1], 300, 100);

    const handles = screen.getAllByRole('separator');
    drag(handles[1], { x: 100, y: 20 }, { x: 150, y: 20 });

    // 外层可分配宽度 1000px,向右 50px = +5%(若把内层也算进去会偏小)
    expect(handles[1]).toHaveAttribute('aria-valuenow', '35');
  });

  it('嵌套分组各自独立拖拽', () => {
    const { container } = render(
      <ResizablePanelGroup>
        <ResizablePanel defaultSize={30}>
          <ResizablePanelGroup direction="vertical">
            <ResizablePanel defaultSize={40}>上</ResizablePanel>
            <ResizableHandle />
            <ResizablePanel>下</ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel>右侧</ResizablePanel>
      </ResizablePanelGroup>,
    );

    const handles = screen.getAllByRole('separator');
    expect(handles).toHaveLength(2);
    expect(
      container.querySelectorAll('[data-slot="resizable-panel-group"]'),
    ).toHaveLength(2);

    fireEvent.keyDown(handles[0], { key: 'ArrowDown' });
    expect(handles[0]).toHaveAttribute('aria-valuenow', '50');
    // 外层分隔条不受内层操作影响
    expect(handles[1]).toHaveAttribute('aria-valuenow', '30');
  });
});
