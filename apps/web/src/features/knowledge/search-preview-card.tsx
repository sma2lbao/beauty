/**
 * 检索预览卡片:检索问题输入、提供方信息与命中片段列表(知识库页右上)。
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
} from '@beauty/design';

import type { KnowledgeModel } from './use-knowledge-model';

export function SearchPreviewCard({ model }: { model: KnowledgeModel }) {
  const { query, setQuery, result, canSearch, searchMutation } = model;

  return (
    <Card>
      <CardHeader>
        <CardTitle>检索预览</CardTitle>
        <CardDescription>
          用向量检索看看资料能不能被找到(排在最前的分数最高)。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-end gap-3">
          <Field className="flex-1" label="检索问题" htmlFor="knowledge-query">
            <Input
              id="knowledge-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="客户 A 的合同评审什么时候?"
            />
          </Field>
          <Button
            onClick={() => searchMutation.mutate()}
            disabled={!canSearch || searchMutation.isPending}
          >
            {searchMutation.isPending ? '检索中…' : '检索'}
          </Button>
        </div>

        {result ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              提供方 {result.provider} / 模型 {result.model},命中{' '}
              {result.hits.length} 条
            </p>
            {result.hits.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                没有命中:换一种问法,或先入库相关资料。
              </p>
            ) : (
              <ul className="space-y-3">
                {result.hits.map((hit) => (
                  <li
                    key={hit.chunk_id}
                    className="rounded-md border px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                      <span>
                        《{hit.title}》#{hit.ordinal}
                      </span>
                      <span>相似度 {hit.score.toFixed(3)}</span>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed">
                      {hit.content}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">还没有检索结果。</p>
        )}
      </CardContent>
    </Card>
  );
}
