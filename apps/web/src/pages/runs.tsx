/**
 * 任务编排(原 /runs):设计稿《02 任务看板 · 多 Agent 编排》的页面组装。
 *
 * 状态到列:待启动(pending/planning)、执行中(running)、
 * 待审核(awaiting_approval/awaiting_confirmation/awaiting_input/blocked)、
 * 已完成(completed/failed)。卡片点开右侧抽屉,原控制台能力都在抽屉里。
 *
 * 数据与视图状态见 @/features/runs/use-runs-model;看板、列、筛选、新建弹窗
 * 分别在 features/runs/run-board.tsx 等文件,本文件只做编辑与布局。
 */
import { Plus } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { PageHeader } from '@/components/console/ui';
import { RunBoard } from '@/features/runs/run-board';
import { RunCreateDialog } from '@/features/runs/run-create-dialog';
import { RunDetail } from '@/features/runs/run-detail';
import { RunFilterMenu } from '@/features/runs/run-filter-menu';
import { useRunsModel } from '@/features/runs/use-runs-model';
import { cn } from '@/lib/utils';

export function RunsPage() {
  const model = useRunsModel();
  const {
    runsQuery,
    selectedRunId,
    setCreateOpen,
    setSelectedRunId,
    subtitle,
    unauthorized,
  } = model;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          title="任务编排"
          subtitle={subtitle}
          actions={
            <>
              <RunFilterMenu model={model} />
              <button
                type="button"
                onClick={() => setCreateOpen(true)}
                className={cn(
                  consoleButtonClass({ variant: 'primary', size: 'md' }),
                  'h-8',
                )}
              >
                <Plus className="h-3.5 w-3.5" />
                新建任务
              </button>
            </>
          }
        />

        {unauthorized ? (
          <div className="mx-6 mb-3 rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning sm:mx-8">
            未登录或令牌已失效:请先
            <Link className="mx-1 underline" to="/login">
              登录
            </Link>
            再重新加载。
          </div>
        ) : null}

        {runsQuery.isError && !unauthorized ? (
          <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
            列表加载失败:{runsQuery.error.message}
          </p>
        ) : null}

        <RunBoard model={model} />
      </div>

      {selectedRunId ? (
        <div className="fixed inset-0 z-30 flex bg-black/40 sm:static sm:z-auto sm:bg-transparent">
          <div className="ml-auto flex h-full w-full max-w-[460px] sm:max-w-none">
            <RunDetail
              runId={selectedRunId}
              onSelectRun={setSelectedRunId}
              onClose={() => setSelectedRunId(null)}
            />
          </div>
        </div>
      ) : null}

      <RunCreateDialog model={model} />
    </div>
  );
}
