/**
 * 右列:待复核候选(R7)。低置信度自动抽取不会直接落库,
 * 通过=写入长期记忆,拒绝=只留痕;两条路径都会刷新统计与列表。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Spin,
  Tag,
} from '@beauty/design';

import type { MemoryKind } from '@/lib/memory-api';

import { KIND_LABELS } from './display';
import { useMemoryReviews } from './use-memory-reviews';

export function MemoryReviewPanel() {
  const { reviews, loading, approve, reject, approvingId, rejectingId, error } =
    useMemoryReviews();

  return (
    <Card>
      <CardHeader>
        <CardTitle>待复核</CardTitle>
        <CardDescription>
          自动抽取的置信度低于阈值时先进入这里;通过后才写入长期记忆。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Spin label="加载中…" />
        ) : reviews.length === 0 ? (
          <Empty description="暂无待复核候选" />
        ) : (
          <ul className="divide-y">
            {reviews.map((review) => {
              const busy =
                approvingId === review.id || rejectingId === review.id;
              return (
                <li key={review.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Tag color="neutral">
                      {KIND_LABELS[review.kind as MemoryKind] ?? review.kind}
                    </Tag>
                    <span className="text-xs text-muted-foreground">
                      置信度 {review.confidence.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-sm">
                    {review.content}
                  </p>
                  {review.evidence_quote ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      依据:{review.evidence_quote}
                    </p>
                  ) : null}
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => approve(review.id)}
                    >
                      {approvingId === review.id ? '写入中…' : '通过'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => reject(review.id)}
                    >
                      {rejectingId === review.id ? '处理中…' : '拒绝'}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {error ? (
          <p className="mt-3 text-sm text-destructive">
            {error instanceof Error ? error.message : '复核队列加载失败'}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
