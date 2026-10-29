import { Empty } from '@beauty/design';

import type { RunEvent } from '@/lib/runs-api';

const TOOL_EVENT = 'run.tool.called.v1';

function toolLine(event: RunEvent): string | null {
  if (event.type !== TOOL_EVENT) {
    return null;
  }
  const name = String(event.payload.name ?? '');
  return `工具 ${name} ${event.payload.ok === true ? '执行成功' : '执行失败'}`;
}

export function RunEvents({ events }: { events: readonly RunEvent[] }) {
  if (events.length === 0) {
    return <Empty description="暂无事件。" />;
  }

  return (
    <ol className="space-y-2" data-testid="run-events">
      {events.map((event) => {
        const tool = toolLine(event);
        return (
          <li key={event.seq} className="rounded-lg border p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs text-muted-foreground">
                #{event.seq}
              </span>
              <span className="font-mono text-xs">{event.type}</span>
            </div>
            {tool ? <p className="mt-1 text-sm">{tool}</p> : null}
            {event.type === TOOL_EVENT && event.payload.detail ? (
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {String(event.payload.detail)}
              </p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
