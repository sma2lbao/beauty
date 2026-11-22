import { afterEach, describe, expect, it, vi } from 'vitest';

import { measureCaretRect } from './caret-rect';

function makeRect(
  left: number,
  top: number,
  width: number,
  height: number,
): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('measureCaretRect', () => {
  it('把镜像里的光标坐标平移回 textarea 在视口中的位置', () => {
    // 镜像固定在视口外 (-9999, 0);光标在镜像内容区 (18, 20)。
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: Element) {
        if (this.tagName === 'SPAN') return makeRect(-9981, 20, 2, 18);
        const element = this as HTMLElement;
        if (element.style.position === 'fixed') {
          return makeRect(-9999, 0, 320, 48);
        }
        return makeRect(120, 520, 320, 48);
      },
    );
    const textarea = document.createElement('textarea');
    textarea.value = '/ne';
    const nodesBefore = document.body.childElementCount;

    expect(measureCaretRect(textarea, textarea.value.length)).toEqual({
      left: 138,
      top: 540,
      bottom: 558,
    });
    // 测量用的镜像必须清理掉
    expect(document.body.childElementCount).toBe(nodesBefore);
  });

  it('textarea 尚未布局(宽度为 0)时回退到输入框左上角', () => {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(
      makeRect(120, 520, 0, 0),
    );
    const textarea = document.createElement('textarea');

    expect(measureCaretRect(textarea, 0)).toEqual({
      left: 132,
      top: 532,
      bottom: 550,
    });
  });
});
