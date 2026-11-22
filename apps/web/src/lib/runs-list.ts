/**
 * run 列表分页(看板与侧栏共用):useInfiniteQuery + 账号级状态流兜底轮询。
 *
 * 缓存键按搜索词分键(['runs','page',q]):同一 q 的两处调用共享一份数据,
 * 不同搜索词互不串页。状态流已连上时列表由推送维护,只保留 30s 兜底
 * (防漏帧/半开连接);未连上(首屏/断网/重连中)仍按兜底间隔拉。
 */
import { useInfiniteQuery } from '@tanstack/react-query';

import { queryKeys } from '@/lib/query-keys';
import { runsApi } from '@/lib/runs-api';
import type { Run, RunPage } from '@/lib/runs-types';
import { useRunsStreamStatus } from '@/lib/runs-stream';

export type RunsListOptions = {
  /** 搜索词(标题/目标);空串/纯空白 = 不加搜索。 */
  q?: string;
  limit?: number;
};

/** 兼容滚动升级与旧测试桩:旧接口返回数组时归一化成 RunPage。 */
function asPage(value: RunPage | Run[]): RunPage {
  return Array.isArray(value) ? { items: value, next_cursor: null } : value;
}

export function useRunsList(options: RunsListOptions = {}) {
  const q = options.q?.trim() ?? '';
  const limit = options.limit ?? 50;
  const stream = useRunsStreamStatus();

  const query = useInfiniteQuery({
    queryKey: queryKeys.runsPage(q),
    queryFn: async ({ pageParam }) =>
      asPage(await runsApi.list({ limit, q, cursor: pageParam })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: stream.connected ? false : 30_000,
  });

  const runs: Run[] =
    query.data?.pages.flatMap((page) => page.items ?? []) ?? [];
  return { query, runs };
}
