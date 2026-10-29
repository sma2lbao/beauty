import { Alert } from '@beauty/design';

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { DocumentListCard } from '@/features/knowledge/document-list-card';
import { IngestCard } from '@/features/knowledge/ingest-card';
import { SearchPreviewCard } from '@/features/knowledge/search-preview-card';
import { useKnowledgeModel } from '@/features/knowledge/use-knowledge-model';

/** 知识库管理台:资料入库、已入库清单、检索预览(§9)。 */
export function KnowledgePage() {
  const model = useKnowledgeModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="知识库"
        subtitle="团队资料入库(分块 + 向量化),Agent 执行时先查资料再作答"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void model.refresh()} />

        {model.unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再操作知识库。
          </Alert>
        ) : null}

        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <IngestCard model={model} />
          <SearchPreviewCard model={model} />
        </div>

        <DocumentListCard model={model} />
      </div>
    </div>
  );
}
