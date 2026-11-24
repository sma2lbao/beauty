/**
 * 需要关注:失败用例与漂移是发布判断的第一入口。
 */
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Link } from 'react-router';

import { ConsolePanel, EmptyPanel, ToneBadge } from '@/components/console/ui';
import type { EvalFailedCase, EvalRunMeta } from '@/lib/eval-api';

function caseLink(runId: string, failed: EvalFailedCase) {
  const query = new URLSearchParams({
    suite: failed.suite,
    case: failed.case_id,
    tab: 'result',
  });
  return '/evals/runs/' + runId + '?' + query.toString();
}

export function AttentionPanel({ latest }: { latest: EvalRunMeta }) {
  const failures = latest.failed_cases.slice(0, 6);

  return (
    <ConsolePanel className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">需要关注</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            失败优先于平均分;漂移不等于失败,但值得复核
          </p>
        </div>
        <ToneBadge tone={failures.length > 0 ? 'danger' : 'success'}>
          {failures.length > 0 ? failures.length + ' 个失败' : '全绿'}
        </ToneBadge>
      </div>

      <div className="min-h-0 flex-1 p-4">
        {failures.length === 0 ? (
          <EmptyPanel
            className="h-full py-8"
            icon={<CheckCircle2 className="h-5 w-5" />}
            title="没有失败用例"
            description={
              latest.drift_count > 0
                ? '用例全部通过;有 ' +
                  latest.drift_count +
                  ' 项基线漂移需要人工确认。'
                : '最近一次运行的用例全部通过,基线也没有漂移。'
            }
          />
        ) : (
          <ul className="space-y-2">
            {failures.map((failure) => (
              <li key={failure.suite + '/' + failure.case_id}>
                <Link
                  to={caseLink(latest.id, failure)}
                  className="block rounded-md border border-ha-border px-3 py-2 transition-colors hover:border-ha-border-strong hover:bg-ha-panel-2/50"
                >
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ha-danger" />
                    <div className="min-w-0">
                      <p className="truncate font-mono text-[11px] text-ha-text">
                        {failure.case_id}
                      </p>
                      <p className="mt-0.5 truncate text-[10px] text-ha-faint">
                        {failure.suite} · {failure.failures[0] ?? '未提供原因'}
                      </p>
                    </div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {latest.drift_count > 0 ? (
        <div className="border-t border-ha-border px-4 py-2 text-[11px] text-ha-warning">
          基线漂移 {latest.drift_count} 项,详情页按套件查看。
        </div>
      ) : null}
    </ConsolePanel>
  );
}
