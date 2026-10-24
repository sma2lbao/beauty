import type { ReactNode } from 'react';

/** 工作区中心的区块标题:图标 + 文案 + 右侧计数。 */
export function SectionHeading({
  icon,
  title,
  hint,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-ha-border px-4 py-2.5">
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-ha-text-2">
        <span className="[&>svg]:h-3.5 [&>svg]:w-3.5 [&>svg]:text-ha-dim">
          {icon}
        </span>
        {title}
      </p>
      <span className="font-mono text-[11px] text-ha-dim">{hint}</span>
    </div>
  );
}
