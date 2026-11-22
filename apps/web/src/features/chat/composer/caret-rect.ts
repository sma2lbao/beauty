/** 光标在视口中的位置;菜单用 fixed 定位,避免被消息列表裁切。 */
export type CaretAnchor = {
  left: number;
  top: number;
  bottom: number;
};

const MIRROR_PROPERTIES = [
  'fontFamily',
  'fontSize',
  'fontWeight',
  'fontStyle',
  'letterSpacing',
  'lineHeight',
  'textTransform',
  'textIndent',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'boxSizing',
] as const;

/**
 * 用隐藏镜像测量 textarea 光标坐标。
 * 浏览器没有原生 caret rect API,这是 CodeMirror/Monaco 之外的通用做法。
 */
export function measureCaretRect(
  textarea: HTMLTextAreaElement,
  caret: number,
): CaretAnchor {
  const rect = textarea.getBoundingClientRect();
  if (typeof document === 'undefined' || rect.width === 0) {
    return {
      left: rect.left + 12,
      top: rect.top + 12,
      bottom: rect.top + 30,
    };
  }
  const computed = window.getComputedStyle(textarea);
  const mirror = document.createElement('div');
  for (const property of MIRROR_PROPERTIES) {
    mirror.style[property] = computed[property];
  }
  mirror.style.position = 'fixed';
  mirror.style.left = '-9999px';
  mirror.style.top = '0';
  mirror.style.width = `${rect.width}px`;
  mirror.style.height = `${rect.height}px`;
  mirror.style.visibility = 'hidden';
  mirror.style.pointerEvents = 'none';
  mirror.style.overflow = 'hidden';
  mirror.style.whiteSpace = 'pre-wrap';
  mirror.style.overflowWrap = 'break-word';
  const span = document.createElement('span');
  span.textContent = textarea.value.slice(0, caret);
  span.appendChild(document.createTextNode('\u200b'));
  mirror.appendChild(span);
  document.body.appendChild(mirror);
  mirror.scrollTop = textarea.scrollTop;
  // 镜像固定在视口外:量到的是镜像自身坐标系,必须平移回 textarea
  // 在视口中的位置,否则菜单会被夹到视口左上角。
  const mirrorRect = mirror.getBoundingClientRect();
  const spanRect = span.getBoundingClientRect();
  document.body.removeChild(mirror);
  const offsetLeft = rect.left - mirrorRect.left;
  const offsetTop = rect.top - mirrorRect.top;
  return {
    left: spanRect.left + offsetLeft,
    top: spanRect.top + offsetTop,
    bottom: spanRect.bottom + offsetTop,
  };
}
