import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Field,
  Input,
  Spin,
  Textarea,
} from '@beauty/design';

import { AuthBar } from '@/components/runs/auth-bar';
import type { SearchResult } from '@/lib/knowledge-api';
import { knowledgeApi } from '@/lib/knowledge-api';
import { isUnauthorized } from '@/lib/runs-api';

const DOCUMENTS_KEY = ['knowledge-documents'];

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

/** 知识库管理台:资料入库、已入库清单、检索预览(§9)。 */
export function KnowledgePage() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [source, setSource] = useState('manual');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);

  const documentsQuery = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: knowledgeApi.documents,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });

  const ingestMutation = useMutation({
    mutationFn: () =>
      knowledgeApi.ingest({
        title: title.trim(),
        content: content.trim(),
        source: source.trim() || 'manual',
      }),
    onSuccess: async () => {
      setTitle('');
      setContent('');
      await refresh();
    },
  });

  const removeMutation = useMutation({
    mutationFn: (documentId: string) => knowledgeApi.remove(documentId),
    onSuccess: refresh,
  });

  const searchMutation = useMutation({
    mutationFn: () => knowledgeApi.search(query.trim()),
    onSuccess: (data) => setResult(data),
  });

  const documents = documentsQuery.data ?? [];
  const errors = [
    documentsQuery.error,
    ingestMutation.error,
    removeMutation.error,
    searchMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canIngest = title.trim().length > 0 && content.trim().length > 0;
  const canSearch = query.trim().length > 0;

  return (
    <section className="mx-auto max-w-6xl px-6 py-12">
      <h1 className="font-display text-3xl">知识库</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        把团队资料入库(服务端分块 + 向量化),Agent 在执行中会通过
        <code className="mx-1 rounded bg-muted px-1">search_knowledge</code>
        先查资料再作答。
      </p>

      <div className="mt-6">
        <AuthBar onChange={() => void refresh()} />
      </div>

      {unauthorized ? (
        <Alert variant="danger" className="mt-4">
          未登录或令牌失效:先在上方面板完成鉴权,再操作知识库。
        </Alert>
      ) : null}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
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
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>已入库资料</CardTitle>
          <CardDescription>
            共 {documents.length} 篇;删除会连带清理其所有片段。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {documentsQuery.isLoading ? (
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
                      {document.chunk_count} 个片段 · {formatSize(document.size_bytes)} ·{' '}
                      {document.embedding_model} · {document.source} ·{' '}
                      {formatTime(document.created_at)}
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
    </section>
  );
}
