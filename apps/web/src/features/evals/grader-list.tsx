/**
 * 评分器列表:显示类型、权重、必选与中文失败原因。
 */
import { CheckCircle2, CircleSlash, XCircle } from 'lucide-react';

import { ConsolePanel } from '@/components/console/ui';
import type { EvalGrader } from '@/lib/eval-api';

export function GraderList({ graders }: { graders: EvalGrader[] }) {
  if (graders.length === 0) {
    return <p className="text-[12px] text-ha-faint">本用例没有评分器记录。</p>;
  }

  return (
    <ConsolePanel className="divide-y divide-ha-border">
      {graders.map((grader) => (
        <div key={grader.id} className="flex items-start gap-2 px-3 py-2">
          <span className="mt-0.5 shrink-0">
            {grader.ok ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-ha-success" />
            ) : grader.required ? (
              <XCircle className="h-3.5 w-3.5 text-ha-danger" />
            ) : (
              <CircleSlash className="h-3.5 w-3.5 text-ha-warning" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-ha-text">
                {grader.id}
              </span>
              <span className="rounded bg-ha-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-ha-muted">
                {grader.type}
              </span>
              <span className="font-mono text-[10px] text-ha-faint">
                weight {grader.weight}
                {grader.required ? ' · required' : ''}
              </span>
            </div>
            {grader.message ? (
              <p className="mt-1 text-[11px] leading-5 text-ha-danger">
                {grader.message}
              </p>
            ) : null}
          </div>
        </div>
      ))}
    </ConsolePanel>
  );
}
