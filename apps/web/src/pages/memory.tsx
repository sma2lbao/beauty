/**
 * 记忆页面:只做组装(页头 + 鉴权栏 + 统计卡 + 列表/检索 + 写入/复核)。
 *
 * 数据查询与变更、局部状态在 features/memory/use-memory-model.ts 与
 * use-memory-reviews.ts;列表、检索、表单等子视图在 features/memory/ 下拆分。
 */
import { Alert, Button } from '@beauty/design';

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { MemoryCreateForm } from '@/features/memory/memory-create-form';
import { MemoryListPanel } from '@/features/memory/memory-list-panel';
import { MemoryReviewPanel } from '@/features/memory/memory-review-panel';
import { MemoryStatCards } from '@/features/memory/memory-stat-cards';
import { useMemoryModel } from '@/features/memory/use-memory-model';

/** 长效记忆管理页:统计、检索、写入、编辑、复核、导出与清理(§7 记忆)。 */
export function MemoryPage() {
  const model = useMemoryModel();
  const {
    stats,
    noticeText,
    unauthorized,
    queryError,
    purgeMutation,
    exportMutation,
    clearAllMutation,
    confirmingClearAll,
    setConfirmingClearAll,
  } = model;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="记忆"
        subtitle="长效记忆:偏好/事实/流程沉淀后可跨会话检索,置顶条目排序靠前,常驻条目始终注入"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => exportMutation.mutate()}
              disabled={exportMutation.isPending}
            >
              {exportMutation.isPending ? '导出中…' : '导出'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => purgeMutation.mutate()}
              disabled={purgeMutation.isPending || (stats?.expired ?? 0) === 0}
            >
              {purgeMutation.isPending ? '清理中…' : '清理过期'}
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => setConfirmingClearAll(true)}
              disabled={clearAllMutation.isPending}
            >
              彻底删除
            </Button>
          </div>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void model.refresh()} />

        {unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再管理记忆。
          </Alert>
        ) : queryError ? (
          <Alert variant="danger" className="mt-4">
            {queryError instanceof Error
              ? queryError.message
              : '记忆数据加载失败'}
          </Alert>
        ) : null}

        {confirmingClearAll ? (
          <Alert variant="danger" className="mt-4">
            <span className="flex flex-wrap items-center justify-between gap-3">
              <span>
                彻底删除会物理清除账号内全部记忆与复核队列,不可恢复。确认继续?
              </span>
              <span className="flex gap-2">
                <Button
                  variant="danger"
                  size="sm"
                  disabled={clearAllMutation.isPending}
                  onClick={() => clearAllMutation.mutate()}
                >
                  {clearAllMutation.isPending ? '删除中…' : '确认彻底删除'}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={clearAllMutation.isPending}
                  onClick={() => setConfirmingClearAll(false)}
                >
                  取消
                </Button>
              </span>
            </span>
          </Alert>
        ) : null}

        {noticeText ? (
          <Alert variant="success" className="mt-4">
            {noticeText}
          </Alert>
        ) : null}

        <MemoryStatCards stats={stats} />

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* 左列:检索 + 列表 */}
          <MemoryListPanel model={model} />
          {/* 右列:写入表单 + 复核队列 */}
          <div className="space-y-6">
            <MemoryCreateForm model={model} />
            <MemoryReviewPanel />
          </div>
        </div>
      </div>
    </div>
  );
}
