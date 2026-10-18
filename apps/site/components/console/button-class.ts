import { cn } from '@/lib/utils';

/**
 * 控制台按钮的类名工厂:页面里的 <Link> 也要长得像按钮时复用它。
 * 单独成文件是为了让 ui.tsx 只导出组件(React Fast Refresh 约束)。
 */
export type ConsoleButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export type ConsoleButtonSize = 'sm' | 'md' | 'lg';

const BUTTON_VARIANTS: Record<ConsoleButtonVariant, string> = {
  primary:
    'bg-ha-accent text-white hover:bg-[#6b77e2] active:bg-[#5560c4] shadow-[0_4px_12px_rgba(94,106,210,0.28)]',
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
