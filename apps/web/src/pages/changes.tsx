import { Download, GitCompareArrows } from 'lucide-react';
import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { ConsoleButton, EmptyPanel, PageHeader } from '@/components/console/ui';
import { ArtifactDetailPanel } from '@/features/changes/artifact-detail-panel';
import { ArtifactListPanel } from '@/features/changes/artifact-list-panel';
import { useChangesModel } from '@/features/changes/use-changes-model';
import { formatBytes } from '@/lib/presentation';

/**
 * 变更与产物(设计稿《03 变更与产物 · Diff 面板》)。
 *
 * 平台里「变更」的真实载体是 workspace 的产物:每个 run 交付的正文/文件元数据。
 * 正文若带 unified diff 标记,按增删行渲染(单栏/并排可切);否则回退成
 * 带行号的代码预览。没有产物时给设计过的空态,不摆假数据。
 */
export function ChangesPage() {
  const model = useChangesModel();
  const {
    artifacts,
    artifactsPending,
    selected,
    totalBytes,
    runCount,
    download,
    downloadPending,
    error,
    unauthorized,
  } = model;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="变更与产物"
        subtitle={
          artifactsPending
            ? '正在读取产物…'
            : artifacts.length +
              ' 个产物 · ' +
              runCount +
              ' 个运行 · 共 ' +
              formatBytes(totalBytes)
        }
        actions={
          <>
            <ConsoleButton
              variant="secondary"
              size="md"
              onClick={download}
              disabled={!selected || downloadPending}
            >
              <Download className="h-3.5 w-3.5" />
              下载产物
            </ConsoleButton>
            <Link
              to="/runs"
              className={consoleButtonClass({ variant: 'primary', size: 'md' })}
            >
              提交评审
            </Link>
          </>
        }
      />

      {unauthorized ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[12px] text-ha-warning sm:mx-8">
          未登录或令牌已失效:请先
          <Link className="mx-1 underline" to="/login">
            登录
          </Link>
          再查看产物。
        </p>
      ) : null}

      {error && !unauthorized ? (
        <p className="mx-6 mb-3 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger sm:mx-8">
          产物加载失败:{error.message}
        </p>
      ) : null}

      {!artifactsPending && artifacts.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center px-6 pb-8 sm:px-8">
          <EmptyPanel
            className="w-full max-w-xl"
            icon={<GitCompareArrows className="h-8 w-8" />}
            title="还没有产物。"
            description="任务完成并交付后,产物会出现在这里:先看看产物列表,再逐条评审。没有 Diff 时按代码正文预览,不会伪造变更行。"
            action={
              <Link
                to="/"
                className={consoleButtonClass({
                  variant: 'primary',
                  size: 'md',
                })}
              >
                去工作台发起任务
              </Link>
            }
          />
        </div>
      ) : null}

      {artifactsPending || artifacts.length > 0 ? (
        <div className="flex min-h-0 flex-1 gap-4 px-6 pb-6 sm:px-8">
          <ArtifactListPanel model={model} />
          <ArtifactDetailPanel model={model} />
        </div>
      ) : null}
    </div>
  );
}
