/**
 * 评测门禁页:已批准基线 × 最新运行,解释基线能否代表当前质量。
 *
 * 只读治理视图:批准要求账号身份,入口在 API;CI 门禁仍以
 * scripts/eval/platform.sh run 的退出码为准。
 */
import { ChevronLeft, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router';

import { EmptyPanel, PageHeader } from '@/components/console/ui';
import { BaselineGates } from '@/features/evals/baseline-gates';
import { formatDateTime, formatShortSha } from '@/features/evals/format';
import { useEvalGatesModel } from '@/features/evals/use-evals-model';

export function EvalGatesPage() {
  const model = useEvalGatesModel();
  const ready = model.rows.filter((row) => row.status === 'ready').length;
  const attention = model.rows.length - ready;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PageHeader
        title="评测门禁"
        subtitle={
          model.latest
            ? '最新运行 ' +
              model.latest.id +
              ' · ' +
              model.latest.branch +
              '@' +
              formatShortSha(model.latest.git_sha) +
              ' · ' +
              formatDateTime(model.latest.created_at)
            : '基线版本、用例完整性与新鲜度'
        }
        actions={
          <div className="flex items-center gap-3">
            <Link
              to="/evals/datasets"
              className="rounded-md border border-ha-border px-2.5 py-1 text-[11px] text-ha-muted transition-colors hover:border-ha-accent hover:text-ha-text"
            >
              数据集
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

      {model.isLoading ? (
        <p className="px-6 text-[12px] text-ha-muted sm:px-8">加载门禁状态…</p>
      ) : null}

      {model.error ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
          门禁数据加载失败:{model.error.message}
          。需要 eval-service(:8009)已启动。
        </p>
      ) : null}

      {!model.isLoading && !model.error && model.rows.length === 0 ? (
        <div className="px-6 sm:px-8">
          <EmptyPanel
            icon={<ShieldCheck className="h-5 w-5" />}
            title="还没有可治理的基线"
            description="先运行 bash scripts/eval/platform.sh run,再通过 POST /api/eval/baselines/{suite} 批准基线。"
          />
        </div>
      ) : null}

      {model.rows.length > 0 ? (
        <>
          <div className="grid gap-3 px-6 pb-4 sm:grid-cols-3 sm:px-8">
            <GateStat
              label="套件"
              value={model.rows.length}
              hint="基线 ∪ 最新运行"
            />
            <GateStat label="就绪" value={ready} hint="可作为当前门禁" />
            <GateStat
              label="需处理"
              value={attention}
              hint="缺基线 / 不可比 / 过期"
              tone={attention > 0 ? 'warning' : 'success'}
            />
          </div>
          <BaselineGates rows={model.rows} runId={model.latest?.id} />
        </>
      ) : null}
    </div>
  );
}

function GateStat({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  hint: string;
  tone?: 'neutral' | 'success' | 'warning';
}) {
  const accent =
    tone === 'success'
      ? 'text-ha-success'
      : tone === 'warning'
        ? 'text-ha-warning'
        : 'text-ha-text';
  return (
    <div className="rounded-lg border border-ha-border bg-ha-sidebar px-4 py-3">
      <p className="text-[10px] uppercase tracking-wide text-ha-faint">
        {label}
      </p>
      <p className={'mt-1 font-mono text-[22px] ' + accent}>{value}</p>
      <p className="mt-0.5 text-[10px] text-ha-faint">{hint}</p>
    </div>
  );
}
