/**
 * 用量只读页(M0-11 本地版):今日/本月 token、请求次数、按模型分布。
 * 无数据时给明确空态;数据由 usage_events 聚合(本地先由 seed/内部上报)。
 */
import { ConsolePanel, PageHeader } from '@/components/console/ui';
import { AvailableModelsPanel } from '@/features/usage/available-models-panel';
import { ModelUsagePanel } from '@/features/usage/model-usage-panel';
import { UsageStatCards } from '@/features/usage/usage-stat-cards';
import { useUsageModel } from '@/features/usage/use-usage-model';

export function UsagePage() {
  const model = useUsageModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="用量"
        subtitle="今日 / 本月 token 与请求次数;模型调用统一经 llm-gateway,按租户聚合"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {model.error ? (
          <ConsolePanel className="mt-4 p-4 text-[13px] text-ha-danger">
            读取用量失败:{model.error.message}
          </ConsolePanel>
        ) : null}

        <UsageStatCards summary={model.summary} />

        <div className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
          <ModelUsagePanel byModel={model.summary?.by_model ?? []} />
          <AvailableModelsPanel
            models={model.models}
            defaultModel={model.defaultModel}
          />
        </div>
      </div>
    </div>
  );
}
