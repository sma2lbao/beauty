/**
 * 复核队列模型(R7):低置信度自动抽取不会直接落库,先进入待复核清单;
 * 通过=写入长期记忆,拒绝=只留痕。列表/变更与记忆页保持同一缓存前缀,
 * 处理成功后统计卡与列表一起刷新。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { isUnauthorized } from '@/lib/http';
import { memoryApi } from '@/lib/memory-api';
import { queryKeys } from '@/lib/query-keys';

/** 一次拉取的待复核条数(后端上限 100)。 */
const REVIEW_LIMIT = 50;

export function useMemoryReviews() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: queryKeys.memoryReviews,
    queryFn: () => memoryApi.reviews(REVIEW_LIMIT),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.memories });
  };

  const approveMutation = useMutation({
    mutationFn: (reviewId: string) => memoryApi.approveReview(reviewId),
    onSuccess: refresh,
  });

  const rejectMutation = useMutation({
    mutationFn: (reviewId: string) => memoryApi.rejectReview(reviewId),
    onSuccess: refresh,
  });

  const error = query.error ?? approveMutation.error ?? rejectMutation.error;

  return {
    reviews: query.data ?? [],
    loading: query.isLoading,
    approvingId: approveMutation.isPending
      ? (approveMutation.variables ?? null)
      : null,
    rejectingId: rejectMutation.isPending
      ? (rejectMutation.variables ?? null)
      : null,
    approve: (reviewId: string) => approveMutation.mutate(reviewId),
    reject: (reviewId: string) => rejectMutation.mutate(reviewId),
    error,
    unauthorized: [
      query.error,
      approveMutation.error,
      rejectMutation.error,
    ].some(isUnauthorized),
  };
}

export type MemoryReviewModel = ReturnType<typeof useMemoryReviews>;
