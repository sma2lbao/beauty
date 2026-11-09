/**
 * 用量页的数据模型:用量汇总与可用模型列表查询。
 * 页面只负责布局,统计卡/模型面板各自只收展示所需字段。
 */
import { useQuery } from '@tanstack/react-query';

import { insightsApi } from '@/lib/insights-api';
import { queryKeys } from '@/lib/query-keys';

export function useUsageModel() {
  const summaryQuery = useQuery({
    queryKey: queryKeys.usageSummary,
    queryFn: insightsApi.usage,
  });
  const modelsQuery = useQuery({
    queryKey: queryKeys.models,
    queryFn: insightsApi.models,
    retry: false,
  });
  // 配额是增强信息:读不到(未配置/端点不可用)只影响配额条,不拖垮整页
  const quotaQuery = useQuery({
    queryKey: queryKeys.usageQuota,
    queryFn: insightsApi.quota,
    retry: false,
  });

  return {
    summary: summaryQuery.data,
    quota: quotaQuery.data,
    models: modelsQuery.data?.data ?? [],
    defaultModel: modelsQuery.data?.default_model,
    error: summaryQuery.error,
  };
}

export type UsageModel = ReturnType<typeof useUsageModel>;
