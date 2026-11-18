import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';
import { toneClass, type Tone } from '../../tone.js';

/**
 * 状态基元:把一个语义 tone 渲染成圆点 / 文字 / 标签 / 描边胶囊。
 * 业务状态到 tone 的映射留在业务层(如运行中 → accent、失败 → danger),
 * 组件只负责形态,颜色一律来自 tone 工具类。
 */

export interface StatusDotProps extends ComponentPropsWithoutRef<'span'> {
  /** 语义色 */
  tone?: Tone;
  /** 是否呼吸闪烁(进行中) */
  pulse?: boolean;
}

export function StatusDot({
  tone = 'neutral',
  pulse = false,
  className,
  ...rest
}: StatusDotProps) {
  return (
    <span
      data-slot="status-dot"
      aria-hidden="true"
      className={cn(
        'bd-status-dot',
        toneClass('dot', tone),
        pulse && 'bd-status-dot--pulse',
        className,
      )}
      {...rest}
    />
  );
}

export interface StatusTextProps extends ComponentPropsWithoutRef<'span'> {
  /** 语义色 */
  tone?: Tone;
}

export function StatusText({
  tone = 'neutral',
  className,
  ...rest
}: StatusTextProps) {
  return (
    <span
      data-slot="status-text"
      className={cn('bd-status-text', toneClass('text', tone), className)}
      {...rest}
    />
  );
}

export interface StatusBadgeProps extends ComponentPropsWithoutRef<'span'> {
  /** 语义色 */
  tone?: Tone;
  /** 等宽显示(编号、版本号、错误码) */
  mono?: boolean;
}

export function StatusBadge({
  tone = 'neutral',
  mono = true,
  className,
  ...rest
}: StatusBadgeProps) {
  return (
    <span
      data-slot="status-badge"
      className={cn(
        'bd-status-badge',
        mono && 'bd-status-badge--mono',
        toneClass('soft', tone),
        className,
      )}
      {...rest}
    />
  );
}

export interface StatusPillProps extends ComponentPropsWithoutRef<'span'> {
  /** 语义色 */
  tone?: Tone;
  /** 圆点是否呼吸闪烁(进行中) */
  pulse?: boolean;
  /** sm = 26px 行高(列表),md = 30px(卡片标题行) */
  size?: 'sm' | 'md';
}

export function StatusPill({
  tone = 'neutral',
  pulse = false,
  size = 'sm',
  className,
  children,
  ...rest
}: StatusPillProps) {
  return (
    <span
      data-slot="status-pill"
      data-size={size}
      className={cn(
        'bd-status-pill',
        size === 'md' && 'bd-status-pill--md',
        toneClass('outline', tone),
        className,
      )}
      {...rest}
    >
      <StatusDot tone={tone} pulse={pulse} />
      {children}
    </span>
  );
}
