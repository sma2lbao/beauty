/**
 * 控制台通用 UI 原语:面板、按钮、状态标签、页头、页签。
 *
 * 这些是「设计稿语言」的零件,集中在一处而不是散落在页面里:
 * - 尺寸/圆角/边框/字重都对齐 ardot 设计稿(按钮 h28~36、面板 radius 8~12);
 * - 状态色只来自 @/lib/presentation 的 tone,页面不写死色值;
 * - 与 @beauty/design 的分工:表单控件/对话框继续用组件库(已跟随 --bd-* 暗色令牌),
 *   这里只补控制台特有的面板、状态标签、工具栏按钮。
 */
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { NavLink } from 'react-router';

import {
  consoleButtonClass,
  type ConsoleButtonSize,
  type ConsoleButtonVariant,
} from '@/components/console/button-class';
import type { Tone } from '@/lib/presentation';
import { RUN_STATUS_LABELS, runStatusTone, toneClasses } from '@/lib/presentation';
import type { RunStatus } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

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

/* ── 按钮 ─────────────────────────────────────────────────────────────── */

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

/* ── 状态标签 / 圆点 ─────────────────────────────────────────────────── */

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
        toneClasses(tone).dot,
        pulse && 'animate-pulse',
        className,
      )}
    />
  );
}

export function ToneBadge({
  tone = 'neutral',
  mono = true,
  className,
  children,
}: {
  tone?: Tone;
  mono?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex h-5 shrink-0 items-center rounded-[5px] px-2 leading-none',
        mono ? 'font-mono text-[11px]' : 'text-[11px] font-medium',
        toneClasses(tone).soft,
        className,
      )}
    >
      {children}
    </span>
  );
}

export function RunStatusBadge({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  return (
    <ToneBadge tone={runStatusTone(status)} className={className}>
      {RUN_STATUS_LABELS[status]}
    </ToneBadge>
  );
}

/** 运行状态胶囊:状态点 + 文案(顶栏用)。 */
export function RunStatusPill({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  const tone = runStatusTone(status);
  return (
    <span
      className={cn(
        'inline-flex h-[30px] items-center gap-[7px] rounded-md border px-[10px] text-[12px] font-medium',
        toneClasses(tone).outline,
        toneClasses(tone).text,
        className,
      )}
    >
      <StatusDot tone={tone} pulse={tone === 'info'} />
      {RUN_STATUS_LABELS[status]}
    </span>
  );
}

/* ── 页头 ─────────────────────────────────────────────────────────────── */

export function PageHeader({
  title,
  subtitle,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header
      className={cn(
        'flex shrink-0 flex-wrap items-center justify-between gap-3 px-6 pb-4 pt-5 sm:px-8',
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="text-[18px] font-semibold tracking-[-0.01em] text-ha-text">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-1 font-mono text-[11px] text-ha-dim">{subtitle}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

/* ── 页签(执行步骤/变更/终端) ─────────────────────────────────────── */

export function ConsoleTabs<T extends string>({
  items,
  value,
  onChange,
  className,
}: {
  items: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn('flex items-center gap-1', className)}
    >
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.value)}
            className={cn(
              'inline-flex h-7 items-center rounded-md px-2.5 text-[12px] transition-colors',
              selected
                ? 'bg-[#1c1d1f] font-semibold text-ha-text'
                : 'text-ha-muted hover:text-ha-text-2',
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
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

export function Avatar({
  name,
  size = 28,
  className,
}: {
  name?: string | null;
  size?: number;
  className?: string;
}) {
  const initial = (name?.trim().charAt(0) || '访').toUpperCase();
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className={cn(
        'inline-grid shrink-0 place-items-center rounded-full bg-[#2a2d35] font-mono text-[11px] font-medium text-ha-muted',
        className,
      )}
    >
      {initial}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-[4px] border border-ha-border-strong bg-ha-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-ha-muted">
      {children}
    </kbd>
  );
}

/** 导航链接:侧边栏与移动端顶栏共用同一套激活态。 */
export function ConsoleNavLink({
  to,
  end,
  icon,
  label,
  compact = false,
}: {
  to: string;
  end?: boolean;
  icon: ReactNode;
  label: string;
  compact?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'flex items-center rounded-md transition-colors',
          compact ? 'h-8 w-8 justify-center' : 'h-8 gap-2.5 px-2.5',
          isActive
            ? 'bg-[#1c1d1f] text-ha-text'
            : 'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
        )
      }
    >
      {({ isActive }) => (
        <>
          <span className="shrink-0 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
          {compact ? (
            <span className="sr-only">{label}</span>
          ) : (
            <span
              className={cn(
                'truncate text-[13px]',
                isActive ? 'font-medium' : 'font-normal',
              )}
            >
              {label}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}
