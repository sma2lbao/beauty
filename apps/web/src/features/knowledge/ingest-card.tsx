/**
 * 入库卡片:标题/来源/正文表单与提交状态(知识库页左上)。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Textarea,
} from '@beauty/design';

import type { KnowledgeModel } from './use-knowledge-model';

export function IngestCard({ model }: { model: KnowledgeModel }) {
  const {
    title,
    setTitle,
    source,
    setSource,
    content,
    setContent,
    canIngest,
    ingestMutation,
  } = model;

  return (
    <Card>
      <CardHeader>
        <CardTitle>资料入库</CardTitle>
        <CardDescription>
          标题与正文必填;正文会被切成若干片段分别向量化。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="资料标题" htmlFor="knowledge-title">
          <Input
            id="knowledge-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="客户 A 合同备忘"
          />
        </Field>
        <Field label="来源" htmlFor="knowledge-source">
          <Input
            id="knowledge-source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder="manual"
          />
        </Field>
        <Field label="正文" htmlFor="knowledge-content">
          <Textarea
            id="knowledge-content"
            className="min-h-40"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="把资料粘贴进来……"
          />
        </Field>
        <div className="flex items-center gap-3">
          <Button
            onClick={() => ingestMutation.mutate()}
            disabled={!canIngest || ingestMutation.isPending}
          >
            {ingestMutation.isPending ? '入库中…' : '入库'}
          </Button>
          {ingestMutation.isSuccess ? (
            <span className="text-sm text-muted-foreground">
              已入库 {ingestMutation.data.chunk_count} 个片段
            </span>
          ) : null}
          {ingestMutation.isError ? (
            <span className="text-sm text-destructive">
              {(ingestMutation.error as Error).message}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
