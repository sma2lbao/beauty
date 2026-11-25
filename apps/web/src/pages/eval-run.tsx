/**
 * 运行详情页:一次评测运行的逐套件、逐用例下钻。
 *
 * URL 是真源:?suite= 套件、?status= 过滤、?case= 抽屉、?tab= 抽屉页签。
 * M0 读 public/eval/runs/<id>.json;后面的 eval-service 不改这套协议。
 */
import { ChevronLeft } from 'lucide-react';
import { Link, useParams } from 'react-router';

import {
  ConsolePanel,
  ConsoleTabs,
  EmptyPanel,
  PageHeader,
} from '@/components/console/ui';
import { EvalCaseDrawer } from '@/features/evals/eval-case-drawer';
import { EvalCaseTable } from '@/features/evals/eval-case-table';
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  formatShortSha,
} from '@/features/evals/format';
import type { EvalCaseStatus } from '@/features/evals/use-evals-model';
import {
  useEvalRunModel,
  useEvalsModel,
} from '@/features/evals/use-evals-model';
import { cn } from '@/lib/utils';

const STATUS_FILTERS: { value: EvalCaseStatus; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
];

export function EvalRunPage() {
  const { runId = '' } = useParams();
  const model = useEvalRunModel(runId);
  const { runs } = useEvalsModel();
  const meta = runs.find((run) => run.id === runId);
  const report = model.run.data;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        <PageHeader
          title="运行详情"
          subtitle={runId || '未知运行'}
          actions={
            <div className="flex items-center gap-3">
              <Link
                to="/evals/compare"
                className="rounded-md border border-ha-border px-2.5 py-1 text-[11px] text-ha-muted transition-colors hover:border-ha-accent hover:text-ha-text"
              >
                对比运行
              </Link>
              <Link
                to="/evals"
                className="inline-flex items-center gap-1 text-[12px] text-ha-muted transition-colors hover:text-ha-text"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                返回评测
              </Link>
            </div>
          }
        />

        {model.run.isLoading ? (
          <p className="px-6 text-[12px] text-ha-muted sm:px-8">
            加载运行快照…
          </p>
        ) : null}

        {model.run.error ? (
          <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
            运行快照加载失败:
            {(model.run.error as Error).message}
          </p>
        ) : null}

        {report ? (
          <>
            <div className="px-6 pb-4 sm:px-8">
              <ConsolePanel className="grid grid-cols-2 gap-px overflow-hidden bg-ha-border md:grid-cols-4">
                <Meta label="触发" value={meta?.trigger ?? 'local'} />
                <Meta
                  label="分支 / SHA"
                  value={
                    (meta?.branch ?? '—') +
                    '@' +
                    formatShortSha(meta?.git_sha ?? '')
                  }
                />
                <Meta label="模型" value={meta?.model ?? '—'} />
                <Meta label="环境" value={meta?.environment ?? '—'} />
                <Meta
                  label="通过率"
                  value={formatPercent(report.totals.pass_rate)}
                />
                <Meta
                  label="tokens"
                  value={formatNumber(report.totals.tokens)}
                />
                <Meta
                  label="耗时"
                  value={formatDuration(report.totals.duration_ms)}
                />
                <Meta
                  label="生成时间"
                  value={formatDateTime(report.generated_at)}
                />
              </ConsolePanel>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 px-6 pb-3 sm:px-8">
              <div className="min-w-0 overflow-x-auto">
                <ConsoleTabs
                  items={model.suites.map((suite) => ({
                    value: suite.name,
                    label: (
                      <span className="flex items-center gap-1.5">
                        {suite.name}
                        <span className="font-mono text-[10px] text-ha-faint">
                          {suite.totals.passed}/{suite.totals.cases}
                        </span>
                      </span>
                    ),
                  }))}
                  value={model.suite}
                  onChange={model.selectSuite}
                />
              </div>

              <div className="flex items-center gap-1">
                {STATUS_FILTERS.map((filter) => (
                  <button
                    key={filter.value}
                    type="button"
                    aria-pressed={model.status === filter.value}
                    onClick={() => model.selectStatus(filter.value)}
                    className={cn(
                      'h-7 rounded-md px-2.5 text-[11px] transition-colors',
                      model.status === filter.value
                        ? 'bg-[#1c1d1f] font-semibold text-ha-text'
                        : 'text-ha-muted hover:text-ha-text-2',
                    )}
                  >
                    {filter.label}
                  </button>
                ))}
                <span className="ml-2 font-mono text-[11px] text-ha-faint">
                  {model.cases.length} 条
                </span>
              </div>
            </div>

            <div className="px-6 pb-8 sm:px-8">
              <EvalCaseTable
                cases={model.cases}
                selectedCaseId={model.selectedCase?.id}
                onSelect={model.selectCase}
              />
            </div>
          </>
        ) : null}

        {!model.run.isLoading && !model.run.error && !report ? (
          <div className="px-6 sm:px-8">
            <EmptyPanel
              title="没有这次运行的快照"
              description="检查 public/eval/runs 下是否有对应 id 的 JSON,或返回评测总览选择最近运行。"
            />
          </div>
        ) : null}
      </div>

      {model.selectedCase ? (
        <div className="fixed inset-0 z-30 flex bg-black/40 sm:static sm:z-auto sm:bg-transparent">
          <div className="ml-auto flex h-full w-full max-w-[680px] sm:max-w-none">
            <EvalCaseDrawer
              testCase={model.selectedCase}
              tab={model.tab}
              onTabChange={model.selectTab}
              onClose={model.closeCase}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ha-sidebar px-3 py-2.5">
      <p className="text-[10px] text-ha-faint">{label}</p>
      <p className="mt-1 truncate font-mono text-[11px] text-ha-text-2">
        {value}
      </p>
    </div>
  );
}
