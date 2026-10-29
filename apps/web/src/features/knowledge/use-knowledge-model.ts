/**
 * 知识库页的数据与交互模型:资料清单查询、入库/删除/检索变更与表单状态。
 * 页面只负责布局与组合,子视图收本模型或窄 props。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { SearchResult } from '@/lib/knowledge-api';
import { knowledgeApi } from '@/lib/knowledge-api';
import { isUnauthorized } from '@/lib/runs-api';
import { queryKeys } from '@/lib/query-keys';

export function useKnowledgeModel() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [source, setSource] = useState('manual');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);

  const documentsQuery = useQuery({
    queryKey: queryKeys.knowledgeDocuments,
    queryFn: knowledgeApi.documents,
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeDocuments });

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

  return {
    // 表单与检索状态
    title,
    setTitle,
    content,
    setContent,
    source,
    setSource,
    query,
    setQuery,
    result,
    // 清单
    documents,
    documentsLoading: documentsQuery.isLoading,
    unauthorized,
    canIngest,
    canSearch,
    // 变更
    ingestMutation,
    removeMutation,
    searchMutation,
    refresh,
  };
}

export type KnowledgeModel = ReturnType<typeof useKnowledgeModel>;
