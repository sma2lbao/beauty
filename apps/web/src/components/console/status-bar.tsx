/**
 * 工作台底部状态栏(26px):分支/事件流/耗时这类「当前上下文」信息。
 * 只展示真实可得的数据——没有 token 计量接口,就不摆假数字。
 */
import type { ReactNode } from 'react';

import type { Tone } from '@/lib/presentation';
import { cn } from '@/lib/utils';

export function ConsoleStatusBar({
  items,
  right,
}: {
  items: { label: string; tone?: Tone }[];
  right?: ReactNode;
}) {
  return (
    <footer className="flex h-[26px] shrink-0 items-center gap-4 overflow-x-auto border-t border-ha-border bg-ha-sidebar px-4 font-mono text-[11px] text-ha-muted">
      {items.map((item) => (
        <span
          key={item.label}
          className={cn(
            'shrink-0 whitespace-nowrap',
            item.tone === 'info' && 'text-ha-info',
            item.tone === 'success' && 'text-ha-success',
            item.tone === 'warning' && 'text-ha-warning',
            item.tone === 'danger' && 'text-ha-danger',
          )}
        >
          {item.label}
        </span>
      ))}
      {right ? <span className="ml-auto shrink-0">{right}</span> : null}
    </footer>
  );
}
