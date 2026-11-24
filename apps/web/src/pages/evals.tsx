/**
 * 评测总览页(M0 静态快照)。
 *
 * 读 public/eval/index.json:最近一次运行的健康度 + 最近运行列表。
 * 运行详情在 /evals/runs/:runId;数据集与门禁治理后续接 eval-service。
 */
import { FlaskConical } from 'lucide-react';

import { PageHeader, EmptyPanel } from '@/components/console/ui';
import { EvalOverview } from '@/features/evals/eval-overview';
import { useEvalsModel } from '@/features/evals/use-evals-model';
import { formatDateTime, formatShortSha } from '@/features/evals/format';

export function EvalsPage() {
  const { latest, runs, suiteRows, isLoading, error } = useEvalsModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PageHeader
        title="评测"
        subtitle={
          latest
            ? '最近运行 ' +
              latest.id +
              ' · ' +
              formatDateTime(latest.created_at) +
              ' · ' +
              latest.branch +
              '@' +
              formatShortSha(latest.git_sha)
            : 'Agent / 检索 / 记忆 / 工具 / 任务韧性'
        }
        actions={
          <span className="rounded-full border border-ha-border px-2.5 py-1 font-mono text-[10px] text-ha-muted">
            offline snapshot
          </span>
        }
      />

      {isLoading ? (
        <p className="px-6 text-[12px] text-ha-muted sm:px-8">加载评测快照…</p>
      ) : null}

      {error ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
          评测快照加载失败:{error.message}
        </p>
      ) : null}

      {!isLoading && !error && !latest ? (
        <div className="px-6 sm:px-8">
          <EmptyPanel
            icon={<FlaskConical className="h-5 w-5" />}
            title="还没有评测快照"
            description="在仓库根运行 bash scripts/eval/platform.sh run,再用 node scripts/eval/publish-web.mjs 发布到 public/eval。"
          />
        </div>
      ) : null}

      {latest ? (
        <EvalOverview latest={latest} runs={runs} suiteRows={suiteRows} />
      ) : null}
    </div>
  );
}
