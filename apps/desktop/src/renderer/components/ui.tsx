/**
 * 控制台 UI 原语:面板、按钮、状态点/标签、空态。
 *
 * 排版手法对齐 apps/web/src/components/console/ui.tsx(设计稿语言):
 * 按钮 h28~36、面板 radius 8~12、状态色只在小面积(状态点/标签)出现。
 * 颜色一律走 --ha-* 映射出的 Tailwind 颜色类(bg-ha-panel 等),不写内联色值。
 */
import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

/* ── 语义色 tone ──────────────────────────────────────────────────────── */

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

const TONE_DOT: Record<Tone, string> = {
  neutral: 'bg-ha-ghost',
  info: 'bg-ha-info',
  success: 'bg-ha-success',
  warning: 'bg-ha-warning',
  danger: 'bg-ha-danger',
};

const TONE_SOFT: Record<Tone, string> = {
  neutral: 'bg-ha-panel-2 text-ha-muted',
  info: 'bg-ha-info-bg text-ha-info',
  success: 'bg-ha-success-bg text-ha-success',
  warning: 'bg-ha-warning-bg text-ha-warning',
  danger: 'bg-ha-danger-bg text-ha-danger',
};

const TONE_OUTLINE: Record<Tone, string> = {
  neutral: 'border-ha-border bg-ha-panel text-ha-muted',
  info: 'border-ha-info-border bg-ha-info-bg text-ha-info',
  success: 'border-ha-success-border bg-ha-success-bg text-ha-success',
  warning: 'border-ha-warning-border bg-ha-warning-bg text-ha-warning',
  danger: 'border-ha-danger-border bg-ha-danger-bg text-ha-danger',
};

const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-ha-muted',
  info: 'text-ha-info',
  success: 'text-ha-success',
  warning: 'text-ha-warning',
  danger: 'text-ha-danger',
};

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

/* ── 状态点 / 标签 ───────────────────────────────────────────────────── */

export function StatusDot({
  tone = 'neutral',
  pulse = false,
  className,
}: {
  tone?: Tone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block h-[7px] w-[7px] shrink-0 rounded-full',
        TONE_DOT[tone],
        pulse && 'animate-pulse',
        className,
      )}
    />
  );
}

export function ToneBadge({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex h-5 shrink-0 items-center rounded-[5px] px-2 font-mono text-[11px] leading-none',
        TONE_SOFT[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** 运行状态胶囊:状态点 + 文案(顶栏用)。 */
export function StatusPill({
  tone = 'neutral',
  pulse = false,
  children,
  className,
}: {
  tone?: Tone;
  pulse?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex h-[26px] items-center gap-[7px] rounded-md border px-2.5 text-[12px] font-medium',
        TONE_OUTLINE[tone],
        className,
      )}
    >
      <StatusDot tone={tone} pulse={pulse} />
      {children}
    </span>
  );
}

export function StatusText({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'font-mono text-[11px] font-medium',
        TONE_TEXT[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ── 空态 ─────────────────────────────────────────────────────────────── */

export function EmptyPanel({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-ha-border px-6 py-12 text-center',
        className,
      )}
    >
      {icon ? <div className="text-ha-ghost">{icon}</div> : null}
      <p className="text-[13px] font-medium text-ha-text-2">{title}</p>
      {description ? (
        <p className="max-w-sm text-[12px] leading-5 text-ha-dim">
          {description}
        </p>
      ) : null}
      {action}
    </div>
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
