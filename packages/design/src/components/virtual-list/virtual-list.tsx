import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type Key,
  type ReactNode,
  type UIEvent,
} from 'react';

import { cn } from '../../lib/utils.js';

/**
 * VirtualList 虚拟列表:只渲染滚动视口附近的项,把 DOM 数量与数据总量解耦。
 *
 * 适用场景是"行高固定或可计算、总量会随使用持续增长"的长列表
 * (会话列表、日志、审计记录);行内需要测量才能得到高度的场景不适用。
 *
 * 实现约定:
 * - 滚动容器就是组件根节点,由调用方用 className / style 给定高度;
 * - 每一项都包在一层绝对定位的 `bd-virtual-list__item` 里,高度与位移来自
 *   itemHeight,所以 renderItem 的内容必须与该高度一致,否则滚动位置会漂移;
 * - 列表语义(role="list" / role="listitem")由调用方通过 props 和 renderItem 提供,
 *   组件自身只负责窗口化,不替业务做可访问性决策。
 */

/** 单项高度:固定数值,或按项与下标计算的函数(变高行)。 */
export type VirtualListItemHeight<T> =
  number | ((item: T, index: number) => number);

export interface VirtualListProps<T> extends Omit<
  ComponentPropsWithoutRef<'div'>,
  'children'
> {
  /** 全量数据;只渲染其中视口附近的一段 */
  items: readonly T[];
  /**
   * 单项高度(px)。变高行传函数时,请在外部保持函数引用稳定
   * (模块级函数或 useCallback),否则每次渲染都会重算全部前缀和。
   */
  itemHeight: VirtualListItemHeight<T>;
  /** 渲染单项内容;外层容器已负责定位与高度 */
  renderItem: (item: T, index: number) => ReactNode;
  /** 视口上下各多渲染几项,减少快速滚动时的空白;默认 4 */
  overscan?: number;
  /** 稳定 key;缺省用下标(数据会增删时请务必传入) */
  getKey?: (item: T, index: number) => Key;
  /** 单项外层容器的附加类名 */
  itemClassName?: string;
  /**
   * 视口尚未测量出来时(SSR 首帧、测试环境)用来估算可见区间的兜底高度。
   * 默认 320。
   */
  fallbackViewportHeight?: number;
}

interface VirtualListMetrics {
  /** 每一项的高度 */
  sizes: number[];
  /** 每一项距离列表顶部的偏移 */
  offsets: number[];
  /** 全部项的总高度,用于撑开滚动条 */
  total: number;
}

/** 二分查找第一个"尚未完全滚出视口顶部"的下标。 */
function findStartIndex(metrics: VirtualListMetrics, scrollTop: number) {
  const { offsets, sizes } = metrics;
  let low = 0;
  let high = offsets.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (offsets[middle] + sizes[middle] <= scrollTop) {
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return Math.min(offsets.length, low);
}

function visibleRange(
  metrics: VirtualListMetrics,
  scrollTop: number,
  viewportHeight: number,
  overscan: number,
) {
  const { offsets, total } = metrics;
  if (offsets.length === 0) return { start: 0, end: 0 };

  const top = Math.min(Math.max(scrollTop, 0), total);
  const first = findStartIndex(metrics, top);
  const bottom = top + Math.max(viewportHeight, 0);

  let last = first;
  while (last < offsets.length && offsets[last] < bottom) {
    last += 1;
  }

  return {
    start: Math.max(0, first - overscan),
    end: Math.min(offsets.length, last + overscan),
  };
}

export function VirtualList<T>({
  items,
  itemHeight,
  renderItem,
  overscan = 4,
  getKey,
  itemClassName,
  fallbackViewportHeight = 320,
  className,
  onScroll,
  ...rest
}: VirtualListProps<T>) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);

  const metrics = useMemo<VirtualListMetrics>(() => {
    const sizes = new Array<number>(items.length);
    const offsets = new Array<number>(items.length);
    let total = 0;

    for (let index = 0; index < items.length; index += 1) {
      const raw =
        typeof itemHeight === 'function'
          ? itemHeight(items[index], index)
          : itemHeight;
      const size = Number.isFinite(raw) ? Math.max(0, raw) : 0;
      sizes[index] = size;
      offsets[index] = total;
      total += size;
    }

    return { sizes, offsets, total };
  }, [items, itemHeight]);

  // 视口高度取真实布局(而不是 window),侧栏折叠、抽屉展开都能跟上。
  // 用 useEffect 而非 useLayoutEffect:服务端渲染时前者不执行,不会告警。
  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;

    const measure = () => setViewportHeight(node.clientHeight);
    measure();

    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // 数据变少时把越界的滚动位置收回,避免停在空白区。
  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;

    const maxScrollTop = Math.max(0, metrics.total - node.clientHeight);
    if (node.scrollTop > maxScrollTop) {
      node.scrollTop = maxScrollTop;
      setScrollTop(maxScrollTop);
    }
  }, [metrics.total]);

  const viewport = viewportHeight > 0 ? viewportHeight : fallbackViewportHeight;
  const { start, end } = visibleRange(metrics, scrollTop, viewport, overscan);

  const handleScroll = (event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
    onScroll?.(event);
  };

  return (
    <div
      ref={scrollerRef}
      data-slot="virtual-list"
      className={cn('bd-virtual-list', className)}
      onScroll={handleScroll}
      {...rest}
    >
      <div
        data-slot="virtual-list-spacer"
        className="bd-virtual-list__spacer"
        style={{ height: metrics.total }}
      >
        {items.slice(start, end).map((item, offset) => {
          const index = start + offset;
          // 定位外壳不参与语义:让 renderItem 里的 listitem 直接归属外层 list
          return (
            <div
              key={getKey ? getKey(item, index) : index}
              role="presentation"
              data-slot="virtual-list-item"
              data-index={index}
              className={cn('bd-virtual-list__item', itemClassName)}
              style={{
                height: metrics.sizes[index],
                transform: `translateY(${metrics.offsets[index]}px)`,
              }}
            >
              {renderItem(item, index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
