import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type {
  ComponentPropsWithoutRef,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from 'react';

import { cn } from '../../lib/utils.js';

export type ResizableDirection = 'horizontal' | 'vertical';

interface PanelMeta {
  /** 初始占比(%),只影响首次布局与双击重置 */
  defaultSize?: number;
  /** 最小占比(%) */
  minSize: number;
  /** 最大占比(%) */
  maxSize: number;
}

interface ResizablePanelGroupContextValue {
  direction: ResizableDirection;
  groupRef: RefObject<HTMLDivElement | null>;
  sizes: Record<string, number>;
  setDragging: (dragging: boolean) => void;
  registerPanel: (id: string, meta: PanelMeta) => void;
  updatePanelMeta: (id: string, meta: PanelMeta) => void;
  unregisterPanel: (id: string) => void;
  resizeAdjacent: (handle: HTMLElement, delta: number) => void;
  resetLayout: () => void;
  getAdjacentInfo: (
    handle: HTMLElement,
  ) => { value: number; min: number; max: number } | null;
}

const ResizablePanelGroupContext =
  createContext<ResizablePanelGroupContextValue | null>(null);

/**
 * 与虚拟列表同样的取舍:服务端渲染时 useLayoutEffect 会告警,
 * 所以服务端退回 useEffect;浏览器端(含水合)仍用布局副作用,
 * 保证尺寸在首帧绘制前就落定,不出现面板塌陷的闪烁。
 */
const useIsomorphicLayoutEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

const DEFAULT_META: PanelMeta = { minSize: 0, maxSize: 100 };

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

const toPercent = (value: number | undefined, fallback: number) => {
  if (value == null || !Number.isFinite(value)) return fallback;
  return clamp(value, 0, 100);
};

const createPanelMeta = (
  defaultSize: number | undefined,
  minSize: number | undefined,
  maxSize: number | undefined,
): PanelMeta => {
  const min = toPercent(minSize, 0);
  const max = Math.max(min, toPercent(maxSize, 100));
  const initial =
    defaultSize == null
      ? undefined
      : clamp(toPercent(defaultSize, min), min, max);
  return { defaultSize: initial, minSize: min, maxSize: max };
};

/**
 * 按「已有尺寸 → 默认尺寸 → 均分」补齐,再把总和收敛到 100%。
 * 每次面板增删都会走这里,已有尺寸尽量保留,新面板用默认值或剩余空间。
 */
function resolveSizes(
  order: string[],
  metas: Map<string, PanelMeta>,
  current: Record<string, number>,
): Record<string, number> {
  const entries = order.map((id) => {
    const meta = metas.get(id) ?? DEFAULT_META;
    const known = current[id];
    const size = known != null ? known : meta.defaultSize;
    return { id, meta, size };
  });

  const missing = entries.filter((entry) => entry.size == null);
  const used = entries.reduce((sum, entry) => sum + (entry.size ?? 0), 0);
  // 缺省尺寸优先吃掉剩余空间;若剩余不足(如已有面板占满 100%),
  // 退回均分,保证新增面板不会以 0 宽出现。
  const remaining = 100 - used;
  const share =
    missing.length > 0
      ? remaining > 1
        ? remaining / missing.length
        : Math.max(remaining / missing.length, 100 / Math.max(order.length, 1))
      : 0;

  const resolved = entries.map((entry) => ({
    id: entry.id,
    meta: entry.meta,
    value: clamp(entry.size ?? share, entry.meta.minSize, entry.meta.maxSize),
  }));

  // 极端约束下(如所有 minSize 之和 > 100)不强求总和 100,
  // 否则会在 min/max 之间反复弹跳。
  for (let pass = 0; pass < 20; pass += 1) {
    const total = resolved.reduce((sum, entry) => sum + entry.value, 0);
    const diff = 100 - total;
    if (Math.abs(diff) < 0.005) break;

    const movable = resolved.filter((entry) =>
      diff > 0
        ? entry.value < entry.meta.maxSize - 0.001
        : entry.value > entry.meta.minSize + 0.001,
    );
    if (movable.length === 0) break;

    const step = diff / movable.length;
    for (const entry of movable) {
      entry.value = clamp(
        entry.value + step,
        entry.meta.minSize,
        entry.meta.maxSize,
      );
    }
  }

  return Object.fromEntries(resolved.map((entry) => [entry.id, entry.value]));
}

/** 分隔条两侧的面板(只认直接子节点里的面板,与主流实现一致)。 */
function findAdjacentPanel(
  handle: HTMLElement,
  direction: 'prev' | 'next',
): HTMLElement | null {
  let node =
    direction === 'prev'
      ? handle.previousElementSibling
      : handle.nextElementSibling;

  while (node) {
    if (node instanceof HTMLElement && node.hasAttribute(PANEL_ATTRIBUTE)) {
      return node;
    }
    node =
      direction === 'prev'
        ? node.previousElementSibling
        : node.nextElementSibling;
  }

  return null;
}

const PANEL_ATTRIBUTE = 'data-resizable-panel-id';

/**
 * 面板可分配的总长度 = 本组直接子面板的实际尺寸之和(不含分隔条本身,
 * 也不含嵌套分组里的面板)。拖拽的百分比位移以它为基准。
 */
function measureAvailable(
  group: HTMLElement,
  direction: ResizableDirection,
): number {
  let total = 0;
  for (const child of group.children) {
    if (!(child instanceof HTMLElement) || !child.hasAttribute(PANEL_ATTRIBUTE))
      continue;
    const rect = child.getBoundingClientRect();
    total += direction === 'horizontal' ? rect.width : rect.height;
  }

  if (total > 0) return total;

  const rect = group.getBoundingClientRect();
  return direction === 'horizontal' ? rect.width : rect.height;
}

export interface ResizablePanelGroupProps extends ComponentPropsWithoutRef<'div'> {
  /** 分栏方向,默认水平 */
  direction?: ResizableDirection;
  /** 尺寸变化(拖拽 / 键盘 / 双击重置)后回调,单位是百分比 */
  onLayout?: (sizes: number[]) => void;
}

/**
 * ResizablePanelGroup 分隔面板组:带可拖拽分隔条的多区域布局。
 *
 * 尺寸用百分比表达,组内面板共享 100%;分隔条支持指针拖拽、
 * 方向键(Shift 微调)与双击重置,遵守 role="separator" 的键盘语义。
 * 面板与分隔条必须是本组件的直接子节点,顺序即布局顺序。
 */
export function ResizablePanelGroup({
  direction = 'horizontal',
  onLayout,
  className,
  children,
  ...rest
}: ResizablePanelGroupProps) {
  const groupRef = useRef<HTMLDivElement>(null);
  const [order, setOrder] = useState<string[]>([]);
  const [sizes, setSizes] = useState<Record<string, number>>({});
  const [dragging, setDragging] = useState(false);

  const orderRef = useRef<string[]>([]);
  const sizesRef = useRef<Record<string, number>>({});
  const metasRef = useRef<Map<string, PanelMeta>>(new Map());
  const onLayoutRef = useRef(onLayout);

  useEffect(() => {
    onLayoutRef.current = onLayout;
  }, [onLayout]);

  const commitSizes = useCallback((next: Record<string, number>) => {
    sizesRef.current = next;
    setSizes(next);
    onLayoutRef.current?.(orderRef.current.map((id) => next[id] ?? 0));
  }, []);

  const registerPanel = useCallback((id: string, meta: PanelMeta) => {
    metasRef.current.set(id, meta);
    setOrder((previous) =>
      previous.includes(id) ? previous : [...previous, id],
    );
  }, []);

  const updatePanelMeta = useCallback((id: string, meta: PanelMeta) => {
    metasRef.current.set(id, meta);
  }, []);

  const unregisterPanel = useCallback((id: string) => {
    metasRef.current.delete(id);
    setOrder((previous) => previous.filter((item) => item !== id));
  }, []);

  const orderKey = order.join('\u0000');

  useIsomorphicLayoutEffect(() => {
    orderRef.current = order;

    if (order.length === 0) {
      if (Object.keys(sizesRef.current).length > 0) {
        sizesRef.current = {};
        setSizes({});
      }
      return;
    }

    const current = sizesRef.current;
    const next = resolveSizes(order, metasRef.current, current);
    const changed =
      Object.keys(current).length !== order.length ||
      order.some(
        (id) => Math.abs((current[id] ?? Number.NaN) - next[id]) > 0.005,
      );

    if (changed) {
      sizesRef.current = next;
      setSizes(next);
    }
  }, [orderKey]);

  const resizeAdjacent = useCallback(
    (handle: HTMLElement, delta: number) => {
      const previous = findAdjacentPanel(handle, 'prev');
      const next = findAdjacentPanel(handle, 'next');
      if (!previous || !next) return;

      const previousId = previous.getAttribute(PANEL_ATTRIBUTE);
      const nextId = next.getAttribute(PANEL_ATTRIBUTE);
      if (!previousId || !nextId) return;

      const previousMeta = metasRef.current.get(previousId) ?? DEFAULT_META;
      const nextMeta = metasRef.current.get(nextId) ?? DEFAULT_META;
      const current = sizesRef.current;
      const previousSize = current[previousId];
      const nextSize = current[nextId];
      if (previousSize == null || nextSize == null) return;

      const lower = Math.max(
        previousMeta.minSize - previousSize,
        nextSize - nextMeta.maxSize,
      );
      const upper = Math.min(
        previousMeta.maxSize - previousSize,
        nextSize - nextMeta.minSize,
      );
      const applied = clamp(delta, lower, upper);
      if (Math.abs(applied) < 0.001) return;

      commitSizes({
        ...current,
        [previousId]: previousSize + applied,
        [nextId]: nextSize - applied,
      });
    },
    [commitSizes],
  );

  const resetLayout = useCallback(() => {
    const currentOrder = orderRef.current;
    if (currentOrder.length === 0) return;
    commitSizes(resolveSizes(currentOrder, metasRef.current, {}));
  }, [commitSizes]);

  const getAdjacentInfo = useCallback((handle: HTMLElement) => {
    const previous = findAdjacentPanel(handle, 'prev');
    const previousId = previous?.getAttribute(PANEL_ATTRIBUTE);
    if (!previousId) return null;

    const value = sizesRef.current[previousId];
    if (value == null) return null;

    const meta = metasRef.current.get(previousId) ?? DEFAULT_META;
    return { value, min: meta.minSize, max: meta.maxSize };
  }, []);

  const contextValue = useMemo<ResizablePanelGroupContextValue>(
    () => ({
      direction,
      groupRef,
      sizes,
      setDragging,
      registerPanel,
      updatePanelMeta,
      unregisterPanel,
      resizeAdjacent,
      resetLayout,
      getAdjacentInfo,
    }),
    [
      direction,
      sizes,
      registerPanel,
      updatePanelMeta,
      unregisterPanel,
      resizeAdjacent,
      resetLayout,
      getAdjacentInfo,
    ],
  );

  return (
    <ResizablePanelGroupContext.Provider value={contextValue}>
      <div
        ref={groupRef}
        data-slot="resizable-panel-group"
        data-direction={direction}
        data-dragging={dragging ? 'true' : undefined}
        className={cn(
          'bd-resizable',
          direction === 'vertical' && 'bd-resizable--vertical',
          className,
        )}
        {...rest}
      >
        {children}
      </div>
    </ResizablePanelGroupContext.Provider>
  );
}

export interface ResizablePanelProps extends ComponentPropsWithoutRef<'div'> {
  /** 初始占比(%),缺省均分;只影响首次布局与双击重置 */
  defaultSize?: number;
  /** 最小占比(%),默认 0 */
  minSize?: number;
  /** 最大占比(%),默认 100 */
  maxSize?: number;
}

/**
 * ResizablePanel 面板:可用空间按百分比增长,内部自带 min-width/height: 0,
 * 因此子内容不会把面板撑破。尺寸由所在的 ResizablePanelGroup 统一管理。
 */
export function ResizablePanel({
  defaultSize,
  minSize,
  maxSize,
  className,
  style,
  children,
  ...rest
}: ResizablePanelProps) {
  const context = useContext(ResizablePanelGroupContext);
  if (!context) {
    throw new Error('ResizablePanel 必须放在 ResizablePanelGroup 内使用');
  }

  const id = useId().replace(/:/g, '');
  const meta = useMemo(
    () => createPanelMeta(defaultSize, minSize, maxSize),
    [defaultSize, minSize, maxSize],
  );

  const { sizes, registerPanel, updatePanelMeta, unregisterPanel } = context;

  useIsomorphicLayoutEffect(() => {
    registerPanel(id, meta);
    return () => unregisterPanel(id);
  }, [id, meta, registerPanel, unregisterPanel]);

  useIsomorphicLayoutEffect(() => {
    updatePanelMeta(id, meta);
  }, [id, meta, updatePanelMeta]);

  // 服务端渲染时还没有 group 的布局结果,先用默认尺寸兜底,
  // 避免首屏面板塌成 0;水合后由 group 的布局效果接管。
  const size = sizes[id] ?? meta.defaultSize ?? 1;

  return (
    <div
      data-slot="resizable-panel"
      data-resizable-panel-id={id}
      data-size={Number(size.toFixed(2))}
      className={cn('bd-resizable__panel', className)}
      style={{ flexGrow: size, flexShrink: 1, flexBasis: 0, ...style }}
      {...rest}
    >
      {children}
    </div>
  );
}

export interface ResizableHandleProps extends ComponentPropsWithoutRef<'div'> {
  /** 显示可见的拖拽手柄 */
  withHandle?: boolean;
  /** 禁用拖拽与键盘调整 */
  disabled?: boolean;
}

/**
 * ResizableHandle 分隔条:指针拖拽 + 方向键调整 + 双击重置。
 *
 * 语义上是 role="separator",aria-valuenow 反映前一个面板的占比;
 * 水平分栏用 ←/→,垂直分栏用 ↑/↓,按住 Shift 每次只调 1%。
 */
export function ResizableHandle({
  withHandle = false,
  disabled = false,
  className,
  onDoubleClick,
  ...rest
}: ResizableHandleProps) {
  const context = useContext(ResizablePanelGroupContext);
  if (!context) {
    throw new Error('ResizableHandle 必须放在 ResizablePanelGroup 内使用');
  }

  const {
    direction,
    sizes,
    setDragging,
    resizeAdjacent,
    resetLayout,
    getAdjacentInfo,
  } = context;

  const handleRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const [adjacent, setAdjacent] = useState<{
    value: number;
    min: number;
    max: number;
  } | null>(null);

  useEffect(
    () => () => {
      cleanupRef.current?.();
    },
    [],
  );

  useIsomorphicLayoutEffect(() => {
    const element = handleRef.current;
    const info = element ? getAdjacentInfo(element) : null;
    if (!info) return;

    const value = Math.round(info.value);
    const min = Math.round(info.min);
    const max = Math.round(info.max);
    setAdjacent((previous) =>
      previous &&
      previous.value === value &&
      previous.min === min &&
      previous.max === max
        ? previous
        : { value, min, max },
    );
  }, [getAdjacentInfo, sizes]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return;

    const element = handleRef.current;
    const group = element?.parentElement;
    if (!element || !group) return;

    const available = measureAvailable(group, direction);
    if (available <= 0) return;

    event.preventDefault();
    let last = direction === 'horizontal' ? event.clientX : event.clientY;
    setDragging(true);

    try {
      element.setPointerCapture?.(event.pointerId);
    } catch {
      // jsdom 或旧浏览器不支持指针捕获,窗口级监听已能兜底。
    }

    const move = (nativeEvent: PointerEvent) => {
      const position =
        direction === 'horizontal' ? nativeEvent.clientX : nativeEvent.clientY;
      const delta = ((position - last) / available) * 100;
      if (!Number.isFinite(delta) || delta === 0) return;
      last = position;
      resizeAdjacent(element, delta);
    };

    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      try {
        element.releasePointerCapture?.(event.pointerId);
      } catch {
        // 同上:没有指针捕获时无需释放。
      }
      cleanupRef.current = null;
      setDragging(false);
    };

    cleanupRef.current = stop;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;

    const step = event.shiftKey ? 1 : 10;
    const horizontal = direction === 'horizontal';
    let delta = 0;

    if (horizontal && event.key === 'ArrowLeft') delta = -step;
    else if (horizontal && event.key === 'ArrowRight') delta = step;
    else if (!horizontal && event.key === 'ArrowUp') delta = -step;
    else if (!horizontal && event.key === 'ArrowDown') delta = step;

    if (delta === 0 || !handleRef.current) return;
    event.preventDefault();
    resizeAdjacent(handleRef.current, delta);
  };

  const handleDoubleClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    onDoubleClick?.(event);
    if (event.defaultPrevented || disabled) return;
    resetLayout();
  };

  return (
    <div
      ref={handleRef}
      role="separator"
      aria-orientation={direction === 'horizontal' ? 'vertical' : 'horizontal'}
      aria-valuenow={adjacent?.value}
      aria-valuemin={adjacent?.min ?? 0}
      aria-valuemax={adjacent?.max ?? 100}
      aria-valuetext={adjacent ? `${adjacent.value}%` : undefined}
      aria-label="调整面板大小"
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      data-slot="resizable-handle"
      data-with-handle={withHandle ? 'true' : undefined}
      className={cn(
        'bd-resizable__handle',
        `bd-resizable__handle--${direction}`,
        disabled && 'bd-resizable__handle--disabled',
        className,
      )}
      onPointerDown={handlePointerDown}
      onKeyDown={handleKeyDown}
      onDoubleClick={handleDoubleClick}
      {...rest}
    >
      {withHandle ? (
        <span className="bd-resizable__grip" aria-hidden="true">
          <svg viewBox="0 0 10 10" focusable="false">
            <circle cx="3.5" cy="2.5" r="0.9" />
            <circle cx="6.5" cy="2.5" r="0.9" />
            <circle cx="3.5" cy="5" r="0.9" />
            <circle cx="6.5" cy="5" r="0.9" />
            <circle cx="3.5" cy="7.5" r="0.9" />
            <circle cx="6.5" cy="7.5" r="0.9" />
          </svg>
        </span>
      ) : null}
    </div>
  );
}
