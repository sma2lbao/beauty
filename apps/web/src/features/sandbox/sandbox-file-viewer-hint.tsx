/**
 * 文件查看器的占位提示(加载中/错误/空态):内容页与改动页共用。
 */
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function Hint({
  children,
  tone = 'dim',
}: {
  children: ReactNode;
  tone?: 'dim' | 'danger';
}) {
  return (
    <p
      className={cn(
        'm-4 rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5',
        tone === 'danger' ? 'text-ha-danger' : 'text-ha-dim',
      )}
    >
      {children}
    </p>
  );
}
