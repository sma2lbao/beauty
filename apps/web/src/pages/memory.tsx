/**
 * 记忆页面:只做组装(页头 + 鉴权栏 + 统计卡 + 列表/检索 + 写入/复核)。
 *
 * 数据查询与变更、局部状态在 features/memory/use-memory-model.ts 与
 * use-memory-reviews.ts;列表、检索、表单等子视图在 features/memory/ 下拆分。
 */
import { Alert, Button } from '@beauty/design';
import { useRef, useState } from 'react';

import { PageHeader } from '@/components/console/ui';
import type { MemoryImportItem } from '@/lib/memory-api';
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
    importMutation,
    reembedMutation,
    clearAllMutation,
    confirmingClearAll,
    setConfirmingClearAll,
  } = model;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  /** 导入文件:接受导出格式(顶层 memories 数组)或裸数组,原样交给后端校验。 */
  const importFile = async (file: File) => {
    setImportError(null);
    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const items = Array.isArray(parsed)
        ? parsed
        : (parsed as { memories?: unknown }).memories;
      if (!Array.isArray(items) || items.length === 0) {
        setImportError('导入文件里没有 memories 数组');
        return;
      }
      importMutation.mutate(items as MemoryImportItem[]);
    } catch {
      setImportError('导入文件不是合法 JSON');
    }
  };

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
              onClick={() => fileInputRef.current?.click()}
              disabled={importMutation.isPending}
            >
              {importMutation.isPending ? '导入中…' : '导入'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => reembedMutation.mutate()}
              disabled={reembedMutation.isPending}
            >
              {reembedMutation.isPending ? '重建中…' : '重建向量'}
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void importFile(file);
              }}
            />
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

        {importError ? (
          <Alert variant="danger" className="mt-4">
            {importError}
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
