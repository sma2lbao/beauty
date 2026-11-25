/**
 * 数据集只读目录页:清单 + 选中数据集 manifest/用例。
 *
 * URL ?dataset= 是选中项唯一真源;数据走 eval-service 的 /v1/datasets,
 * 不回退静态快照(快照只含运行结果,不含数据集定义)。
 */
import { ChevronLeft, Database } from 'lucide-react';
import { Link } from 'react-router';

import { EmptyPanel, PageHeader } from '@/components/console/ui';
import { DatasetCatalog } from '@/features/evals/dataset-catalog';
import { useEvalDatasetsModel } from '@/features/evals/use-evals-model';

export function EvalDatasetsPage() {
  const model = useEvalDatasetsModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PageHeader
        title="评测数据集"
        subtitle={
          model.items.length > 0
            ? model.items.length +
              ' 个数据集 · ' +
              model.items.filter((item) => item.valid).length +
              ' 个可用'
            : '用例、输入与 grader 的版本化定义'
        }
        actions={
          <div className="flex items-center gap-3">
            <Link
              to="/evals/gates"
              className="rounded-md border border-ha-border px-2.5 py-1 text-[11px] text-ha-muted transition-colors hover:border-ha-accent hover:text-ha-text"
            >
              基线门禁
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
        <p className="px-6 text-[12px] text-ha-muted sm:px-8">加载数据集…</p>
      ) : null}

      {model.error ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
          数据集清单加载失败:{model.error.message}
          。需要 eval-service(:8009)已启动。
        </p>
      ) : null}

      {!model.isLoading && !model.error && model.items.length === 0 ? (
        <div className="px-6 sm:px-8">
          <EmptyPanel
            icon={<Database className="h-5 w-5" />}
            title="还没有数据集"
            description="在 evals/datasets/<name>/ 下放 manifest.json 与 cases.jsonl,eval-service 会自动收录。"
          />
        </div>
      ) : null}

      {model.items.length > 0 ? (
        <DatasetCatalog
          items={model.items}
          selected={model.selectedName}
          detail={model.detail.data}
          detailLoading={model.detail.isLoading}
          detailError={model.detail.error as Error | null}
        />
      ) : null}
    </div>
  );
}
