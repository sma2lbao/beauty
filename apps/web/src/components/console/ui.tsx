/**
 * 控制台通用 UI 原语:面板、按钮、页头、页签与状态展示。
 *
 * 这些是「设计稿语言」的零件,集中在一处而不是散落在页面里:
 * - 尺寸/圆角/边框/字重都对齐 ardot 设计稿(按钮 h28~36、面板 radius 8~12);
 * - 状态色只来自 @/lib/presentation 的 tone,页面不写死色值;
 * - 与 @beauty/design 的分工(R-26):Button/Dialog/Table/DropdownMenu 与
 *   状态基元(Status*)、Avatar、Kbd 一律用组件库,本文件只做
 *   「控制台 prop 名 → 组件库 prop」的薄适配,并保留面板、页头、页签、空态
 *   这些控制台外壳专属排版;不再自建按钮/对话框/状态色的视觉与行为。
 */
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { NavLink } from 'react-router';

import { Button, StatusBadge, StatusPill } from '@beauty/design';

import { EmptyPanel } from '@/components/console/empty-panel';

import type {
  ConsoleButtonSize,
  ConsoleButtonVariant,
} from '@/components/console/button-class';
import { RUN_STATUS_LABELS, runStatusTone } from '@/lib/presentation';
import type { RunStatus } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

/** 状态基元与空态:前者来自 @beauty/design,EmptyPanel 是控制台自有排版(见 empty-panel.tsx) */
export { EmptyPanel };
export { Avatar, Kbd, StatusDot } from '@beauty/design';

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

/**
 * 控制台按钮:纯适配层,行为/可访问性/视觉全部来自 @beauty/design 的 Button
 * (含 disabled/loading/asChild/ref)。变体名与尺寸名是控制台历史 API 的子集,
 * 直通组件库同名变体;Link 版按钮用 button-class.ts 的同一份配方保持视觉一致。
 */
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
    <Button
      type={type}
      variant={variant}
      size={size}
      className={className}
      {...rest}
    >
      {children}
    </Button>
  );
}

/* ── 状态标签 / 圆点 ─────────────────────────────────────────────────── */

/** 语义标签:默认等宽(状态/编号),旧名保留,实现在组件库 StatusBadge */
export { StatusBadge as ToneBadge };

export function RunStatusBadge({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  return (
    <StatusBadge tone={runStatusTone(status)} className={className}>
      {RUN_STATUS_LABELS[status]}
    </StatusBadge>
  );
}

/** 运行状态胶囊:状态点 + 文案(顶栏用),md 高度与标题基线对齐。 */
export function RunStatusPill({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  const tone = runStatusTone(status);
  return (
    <StatusPill
      tone={tone}
      size="md"
      pulse={tone === 'info'}
      className={className}
    >
      {RUN_STATUS_LABELS[status]}
    </StatusPill>
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
    <div role="tablist" className={cn('flex items-center gap-1', className)}>
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

/** 导航链接:侧边栏与移动端顶栏共用同一套激活态。 */
export function ConsoleNavLink({
  to,
  end,
  icon,
  label,
  compact = false,
  onClick,
}: {
  to: string;
  end?: boolean;
  icon: ReactNode;
  label: string;
  compact?: boolean;
  /** 收起承载它的弹层等副作用:点击即触发,导航仍由 NavLink 负责。 */
  onClick?: () => void;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
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
