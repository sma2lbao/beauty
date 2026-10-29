/**
 * 已入库资料卡片:清单、加载/空态与删除动作(知识库页底部)。
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
} from '@beauty/design';

import { formatDateTime, formatSize } from '@/lib/presentation';

import type { KnowledgeModel } from './use-knowledge-model';

export function DocumentListCard({ model }: { model: KnowledgeModel }) {
  const { documents, documentsLoading, removeMutation } = model;

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>已入库资料</CardTitle>
        <CardDescription>
          共 {documents.length} 篇;删除会连带清理其所有片段。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {documentsLoading ? (
          <Spin label="加载中…" />
        ) : documents.length === 0 ? (
          <Empty description="还没有资料:先在上方入库一篇。" />
        ) : (
          <ul className="divide-y">
            {documents.map((document) => (
              <li
                key={document.id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <div>
                  <p className="font-medium">{document.title}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {document.chunk_count} 个片段 ·{' '}
                    {formatSize(document.size_bytes)} ·{' '}
                    {document.embedding_model} · {document.source} ·{' '}
                    {formatDateTime(document.created_at)}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => removeMutation.mutate(document.id)}
                  disabled={removeMutation.isPending}
                >
                  删除
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
