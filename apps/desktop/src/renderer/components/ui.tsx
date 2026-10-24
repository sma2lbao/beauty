/**
 * 控制台 UI 原语:面板、按钮、分节标题、图标底座。
 *
 * 排版手法对齐 apps/web/src/components/console/ui.tsx(设计稿语言):
 * 按钮 h28~36、面板 radius 8~12、状态色只在小面积(状态点/标签)出现。
 * 颜色一律走 --ha-* 映射出的 Tailwind 颜色类(bg-ha-panel 等),不写内联色值。
 *
 * 状态基元(StatusDot/StatusText/ToneBadge/StatusPill/EmptyPanel)与 cn
 * 已收敛到 @beauty/console-ui:这里 re-export 保持应用内 '@/components/ui'
 * 引用面不变,只保留桌面端特有的面板/按钮/图标底座。
 */
import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

export {
  EmptyPanel,
  StatusDot,
  StatusPill,
  StatusText,
  ToneBadge,
} from '@beauty/console-ui';
export type { Tone } from '@beauty/console-ui';

/* ── 面板 ─────────────────────────────────────────────────────────────── */

export function ConsolePanel({
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<'div'>) {
  return (
    <div
      className={cn(
        'rounded-lg border border-ha-border bg-ha-sidebar',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/** 区块小标题(设置页/文件页的分节标签)。 */
export function SectionLabel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        'font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-ha-faint',
        className,
      )}
    >
      {children}
    </p>
  );
}

/* ── 按钮 ─────────────────────────────────────────────────────────────── */

export type ConsoleButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export type ConsoleButtonSize = 'sm' | 'md' | 'lg';

const BUTTON_VARIANTS: Record<ConsoleButtonVariant, string> = {
  primary:
    'bg-ha-accent text-white hover:bg-ha-accent-soft active:bg-ha-accent shadow-[0_4px_12px_rgba(94,106,210,0.24)]',
  secondary:
    'border border-ha-border-strong bg-ha-panel-2 text-ha-text-2 hover:border-ha-ghost hover:text-ha-text',
  ghost: 'text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text',
  danger:
    'border border-ha-danger-border bg-ha-danger-bg text-ha-danger hover:border-ha-danger',
};

const BUTTON_SIZES: Record<ConsoleButtonSize, string> = {
  sm: 'h-7 px-3 text-[12px]',
  md: 'h-8 px-3 text-[12px]',
  lg: 'h-9 px-4 text-[13px]',
};

export function consoleButtonClass({
  variant = 'secondary',
  size = 'md',
  className,
}: {
  variant?: ConsoleButtonVariant;
  size?: ConsoleButtonSize;
  className?: string;
} = {}): string {
  return cn(
    'inline-flex select-none items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-45',
    BUTTON_VARIANTS[variant],
    BUTTON_SIZES[size],
    className,
  );
}

export function ConsoleButton({
  variant = 'secondary',
  size = 'md',
  className,
  type = 'button',
  children,
  ...rest
}: ComponentPropsWithoutRef<'button'> & {
  variant?: ConsoleButtonVariant;
  size?: ConsoleButtonSize;
}) {
  return (
    <button
      type={type}
      className={consoleButtonClass({ variant, size, className })}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ── 小零件 ───────────────────────────────────────────────────────────── */

export function Mono({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn('font-mono text-[11px] text-ha-dim', className)}>
      {children}
    </span>
  );
}

/** 工作区/会话路径用的小图标底座。 */
export function IconBox({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'grid h-6 w-6 shrink-0 place-items-center rounded-md bg-ha-panel-2 text-ha-muted',
        className,
      )}
    >
      {children}
    </span>
  );
}
