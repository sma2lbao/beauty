/**
 * 工具轨迹:按发生顺序展示调用、参数、风险、决策与错误。
 */
import { ArrowRight, Wrench } from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';

export type ToolCallDetail = {
  name: string;
  arguments?: unknown;
  ok?: boolean;
  risk?: string | null;
  decision?: string | null;
  error_code?: string | null;
  duration_ms?: number | null;
};

export function ToolTrajectory({
  events,
  details,
}: {
  events: string[];
  details: ToolCallDetail[];
}) {
  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
          事件序列
        </h3>
        <ConsolePanel className="max-h-56 overflow-y-auto p-2">
          <ol className="space-y-1">
            {events.map((event, index) => (
              <li
                key={event + index}
                className="flex items-center gap-2 font-mono text-[10px] text-ha-muted"
              >
                <span className="w-6 shrink-0 text-right text-ha-faint">
                  {index + 1}
                </span>
                <ArrowRight className="h-3 w-3 shrink-0 text-ha-ghost" />
                <span className="truncate">{event}</span>
              </li>
            ))}
          </ol>
          {events.length === 0 ? (
            <p className="p-2 text-[11px] text-ha-faint">没有事件记录。</p>
          ) : null}
        </ConsolePanel>
      </section>

      <section>
        <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
          工具调用
        </h3>
        {details.length === 0 ? (
          <p className="text-[11px] text-ha-faint">本用例没有工具调用。</p>
        ) : (
          <ol className="space-y-2">
            {details.map((detail, index) => (
              <li key={detail.name + index}>
                <ConsolePanel className="p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Wrench className="h-3.5 w-3.5 text-ha-accent-soft" />
                    <span className="font-mono text-[11px] text-ha-text">
                      {detail.name}
                    </span>
                    <span
                      className={
                        'rounded px-1.5 py-0.5 font-mono text-[10px] ' +
                        (detail.ok
                          ? 'bg-ha-success-bg text-ha-success'
                          : 'bg-ha-danger-bg text-ha-danger')
                      }
                    >
                      {detail.ok ? 'ok' : 'failed'}
                    </span>
                    {detail.risk ? (
                      <span className="font-mono text-[10px] text-ha-muted">
                        risk={detail.risk}
                      </span>
                    ) : null}
                    {detail.decision ? (
                      <span className="font-mono text-[10px] text-ha-muted">
                        decision={detail.decision}
                      </span>
                    ) : null}
                    {detail.duration_ms != null ? (
                      <span className="ml-auto font-mono text-[10px] text-ha-faint">
                        {detail.duration_ms}ms
                      </span>
                    ) : null}
                  </div>
                  <pre className="mt-2 overflow-x-auto rounded bg-ha-bg p-2 font-mono text-[10px] leading-5 text-ha-text-2">
                    {JSON.stringify(detail.arguments ?? {}, null, 2)}
                  </pre>
                  {detail.error_code ? (
                    <p className="mt-2 font-mono text-[10px] text-ha-danger">
                      {detail.error_code}
                    </p>
                  ) : null}
                </ConsolePanel>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
