/**
 * 管理台「成本」页签(P1-K)的数据模型:平台成本汇总(按天/来源/模型/Top 账号)。
 * 契约唯一真源:docs/design/eval-and-cost-dashboard.md §3.2/§4;读端点不写审计。
 *
 * 查询只在页签挂载时启用(enabled);403/401 提升成整页闸门口径
 * (与 AdminAccessPanel 一致),其他错误留在面板内就地提示 + 重试。
 */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { adminApi } from '@/lib/admin-api';
import { ApiError, isUnauthorized } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

/** 契约允许的窗口天数:7 / 30 / 90,默认 30。 */
export const ADMIN_COST_RANGES = [7, 30, 90] as const;
export type AdminCostRange = (typeof ADMIN_COST_RANGES)[number];
export const ADMIN_COST_DEFAULT_RANGE: AdminCostRange = 30;

/** 只有鉴权类错误值得升级成整页闸门;null 表示错误留在面板内。 */
export type AdminCostAccess = 'unauthorized' | 'forbidden' | null;

function costAccessFromError(error: unknown): AdminCostAccess {
  if (isUnauthorized(error)) {
    return 'unauthorized';
  }
  if (error instanceof ApiError && error.status === 403) {
    return 'forbidden';
  }
  return null;
}

export function useAdminCostModel({ enabled }: { enabled: boolean }) {
  const [range, setRange] = useState<AdminCostRange>(ADMIN_COST_DEFAULT_RANGE);

  const query = useQuery({
    queryKey: queryKeys.adminCostSummary(range),
    queryFn: () => adminApi.costSummary(range),
    enabled,
  });

  return {
    range,
    setRange,
    data: query.data,
    isLoading: query.isPending,
    error: query.error,
    access: costAccessFromError(query.error),
    refetch: () => void query.refetch(),
  };
}

export type AdminCostModel = ReturnType<typeof useAdminCostModel>;
