/**
 * 状态基元:状态点、状态文字、语义标签、状态胶囊、空态。
 *
 * 原先 apps/web(components/console/ui.tsx)与 apps/desktop(components/ui.tsx)
 * 各维护一份近似实现;抽到共享包后 props 保持既有调用方语义不变。
 * 颜色只来自 ./tone,不在这里写死色值。
 */
import type { ReactNode } from 'react';

import { cn } from './cn.js';
import { toneClasses, type Tone } from './tone.js';

/** 状态点:小圆点,信息态默认呼吸。 */
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

/** 等宽状态文字(工具卡片/元信息)。 */
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
        toneClasses(tone).text,
        className,
      )}
    >
      {children}
    </span>
  );
}

/** 语义标签:默认等宽,适合状态/编号。 */
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

/** 状态胶囊:状态点 + 文案(顶栏用)。 */
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
  const classes = toneClasses(tone);
  return (
    <span
      className={cn(
        'inline-flex h-[26px] items-center gap-[7px] rounded-md border px-2.5 text-[12px] font-medium',
        classes.outline,
        classes.text,
        className,
      )}
    >
      <StatusDot tone={tone} pulse={pulse} />
      {children}
    </span>
  );
}

/** 空态:图标 + 标题 + 说明 + 动作。 */
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
