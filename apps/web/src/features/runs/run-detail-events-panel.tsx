/**
 * 事件流面板:SSE 连接状态点 + 事件列表(数据由 use-run-events 订阅)。
 */
import { ConsolePanel } from '@/components/console/ui';
import { RunEvents } from '@/features/runs/run-events';
import type { RunEvent } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

export function RunDetailEventsPanel({
  events,
  connected,
}: {
  events: readonly RunEvent[];
  connected: boolean;
}) {
  return (
    <ConsolePanel className="p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">事件流</span>
        <span
          className={cn(
            'inline-flex items-center gap-1.5 font-mono text-[11px]',
            connected ? 'text-ha-success' : 'text-ha-dim',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              connected ? 'animate-pulse bg-ha-success' : 'bg-ha-ghost',
            )}
          />
          {connected ? 'SSE 已连接' : '已断开'}
        </span>
      </div>
      <div className="mt-2 max-h-72 overflow-y-auto">
        <RunEvents events={events} />
      </div>
    </ConsolePanel>
  );
}
