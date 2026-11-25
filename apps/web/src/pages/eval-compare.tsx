/**
 * 跨运行对比页:选基线与候选,定位回归与修复。
 *
 * URL 是真源:?base=&candidate=&status=。数据走 eval-service 的 /v1/compare,
 * 不再回退静态快照(静态快照只有单次运行,没有跨运行视图)。
 */
import { ChevronLeft } from 'lucide-react';
import { Link } from 'react-router';

import { EmptyPanel, PageHeader } from '@/components/console/ui';
import { CompareView } from '@/features/evals/compare-view';
import { useEvalCompareModel } from '@/features/evals/use-evals-model';

export function EvalComparePage() {
  const model = useEvalCompareModel();
  const compare = model.compare.data;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PageHeader
        title="运行对比"
        subtitle={
          compare
            ? model.baseId.slice(0, 8) + ' → ' + model.candidateId.slice(0, 8)
            : '基线 → 候选 · 回归 / 修复 / 用例集合变化'
        }
        actions={
          <Link
            to="/evals"
            className="inline-flex items-center gap-1 text-[12px] text-ha-muted transition-colors hover:text-ha-text"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            返回评测
          </Link>
        }
      />

      {model.index.isLoading ? (
        <p className="px-6 text-[12px] text-ha-muted sm:px-8">加载运行列表…</p>
      ) : null}

      {model.index.error ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
          运行列表加载失败:{(model.index.error as Error).message}
        </p>
      ) : null}

      {!model.index.isLoading &&
      !model.index.error &&
      model.runs.length === 0 ? (
        <div className="px-6 sm:px-8">
          <EmptyPanel
            title="还没有可对比的运行"
            description="先运行 bash scripts/eval/platform.sh run,再用 node scripts/eval/upload.mjs 上报到 eval-service。"
          />
        </div>
      ) : null}

      {model.runs.length > 0 && model.compare.error ? (
        <div className="px-6 sm:px-8">
          <EmptyPanel
            title="对比加载失败"
            description={
              (model.compare.error as Error).message +
              '。需要 eval-service(:8009)已启动;静态快照不提供跨运行对比。'
            }
          />
        </div>
      ) : null}

      {compare ? (
        <CompareView
          compare={compare}
          runs={model.runs}
          baseId={model.baseId}
          candidateId={model.candidateId}
          status={model.status}
          cases={model.cases}
          selectBase={model.selectBase}
          selectCandidate={model.selectCandidate}
          selectStatus={model.selectStatus}
        />
      ) : null}

      {model.runs.length > 0 && model.compare.isLoading ? (
        <p className="px-6 text-[12px] text-ha-muted sm:px-8">加载对比…</p>
      ) : null}
    </div>
  );
}
