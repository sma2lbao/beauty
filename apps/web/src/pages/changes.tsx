import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Check,
  Copy,
  Download,
  FileCode2,
  GitCompareArrows,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { ArtifactPreview } from '@/components/console/artifact-preview';
import { DiffViewer } from '@/components/console/diff-view';
import {
  ConsoleButton,
  ConsolePanel,
  EmptyPanel,
  PageHeader,
  StatusDot,
  ToneBadge,
} from '@/components/console/ui';
import { consoleButtonClass } from '@/components/console/button-class';
import {
  artifactFileName,
  isImageArtifact,
  isTextArtifact,
  saveBlob,
} from '@/lib/artifacts';
import {
  diffStats,
  formatBytes,
  parseUnifiedDiff,
  relativeTime,
} from '@/lib/presentation';
import { workspaceApi } from '@/lib/workspace-api';
import { isUnauthorized } from '@/lib/runs-api';
import { useBlobUrl } from '@/lib/use-blob-url';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/query-keys';

type ViewMode = 'inline' | 'split';

/**
 * 变更与产物(设计稿《03 变更与产物 · Diff 面板》)。
 *
 * 平台里「变更」的真实载体是 workspace 的产物:每个 run 交付的正文/文件元数据。
 * 正文若带 unified diff 标记,按增删行渲染(单栏/并排可切);否则回退成
 * 带行号的代码预览。没有产物时给设计过的空态,不摆假数据。
 */
export function ChangesPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>('inline');
  const [copied, setCopied] = useState(false);

  const workspaceQuery = useQuery({
    queryKey: queryKeys.defaultWorkspace,
    queryFn: workspaceApi.defaultWorkspace,
    retry: false,
  });
  const workspace = workspaceQuery.data;
  const artifactsQuery = useQuery({
    queryKey: queryKeys.workspaceArtifacts(workspace?.id),
    queryFn: () => workspaceApi.artifacts(workspace?.id as string),
    enabled: Boolean(workspace?.id),
    refetchInterval: 8000,
  });

  const artifacts = useMemo(
    () => artifactsQuery.data ?? [],
    [artifactsQuery.data],
  );
  const selected =
    artifacts.find((item) => item.id === selectedId) ?? artifacts[0] ?? null;

  const isImage = selected ? isImageArtifact(selected) : false;
  const isText = selected ? isTextArtifact(selected) : false;

  const contentQuery = useQuery({
    queryKey: queryKeys.workspaceArtifact(selected?.id),
    queryFn: () => workspaceApi.artifact(selected?.id as string),
    enabled: Boolean(selected?.id) && !isImage,
  });
  const content = contentQuery.data?.content ?? '';
  const imageQuery = useQuery({
    queryKey: queryKeys.workspaceArtifactBlob(selected?.id),
    queryFn: () => workspaceApi.downloadArtifact(selected?.id as string),
    enabled: Boolean(selected?.id) && isImage,
    retry: false,
  });
  const imageUrl = useBlobUrl(imageQuery.data ?? null);
  const isDiff = Boolean(parseUnifiedDiff(content));
  const stats = diffStats(content);
  const totalBytes = artifacts.reduce((sum, item) => sum + item.size_bytes, 0);
  const runCount = new Set(artifacts.map((item) => item.run_id).filter(Boolean))
    .size;

  const error =
    workspaceQuery.error ?? artifactsQuery.error ?? contentQuery.error;
  const unauthorized = isUnauthorized(error);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用时静默:下载仍可用
    }
  };

  const downloadMutation = useMutation({
    mutationFn: async () => {
      if (!selected) return;
      const blob = await workspaceApi.downloadArtifact(selected.id);
      saveBlob(blob, artifactFileName(selected));
    },
  });
  const download = () => downloadMutation.mutate();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="变更与产物"
        subtitle={
          artifactsQuery.isPending
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
              disabled={!selected || downloadMutation.isPending}
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

      {!artifactsQuery.isPending && artifacts.length === 0 ? (
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

      {artifactsQuery.isPending || artifacts.length > 0 ? (
        <div className="flex min-h-0 flex-1 gap-4 px-6 pb-6 sm:px-8">
          <ConsolePanel className="hidden w-[300px] shrink-0 flex-col p-3 md:flex">
            <div className="flex items-center justify-between px-1.5 pb-2.5 pt-0.5">
              <span className="text-[13px] font-semibold text-ha-text">
                产物列表
              </span>
              <ToneBadge tone="neutral">{artifacts.length}</ToneBadge>
            </div>
            <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {artifactsQuery.isPending
                ? [0, 1, 2, 3].map((key) => (
                    <div
                      key={key}
                      className="h-12 animate-pulse rounded-md bg-ha-panel"
                    />
                  ))
                : null}
              {artifacts.map((artifact) => {
                const active = artifact.id === selected?.id;
                return (
                  <button
                    key={artifact.id}
                    type="button"
                    onClick={() => setSelectedId(artifact.id)}
                    aria-pressed={active}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors',
                      active ? 'bg-[#1c1d1f]' : 'hover:bg-ha-panel-2/60',
                    )}
                  >
                    <FileCode2
                      aria-hidden="true"
                      className={cn(
                        'h-3.5 w-3.5 shrink-0',
                        active ? 'text-ha-accent-soft' : 'text-ha-muted',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          'block truncate font-mono text-[11px]',
                          active
                            ? 'font-medium text-ha-text'
                            : 'text-ha-text-2',
                        )}
                      >
                        {artifact.filename ?? artifact.title}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-ha-faint">
                        {artifact.kind} · {formatBytes(artifact.size_bytes)}
                        {artifact.content_type
                          ? ' · ' + artifact.content_type
                          : null}
                      </span>
                    </span>
                    {artifact.run_id ? (
                      <span className="shrink-0 font-mono text-[10px] text-ha-faint">
                        #{artifact.run_id.slice(-4).toUpperCase()}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </ConsolePanel>

          <ConsolePanel className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-ha-border px-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="truncate font-mono text-[12px] font-medium text-ha-text">
                  {selected?.title ?? '选择一个产物'}
                </span>
                {selected ? (
                  <ToneBadge tone="success">{selected.kind}</ToneBadge>
                ) : null}
                {isDiff && stats ? (
                  <span className="hidden items-center gap-2 font-mono text-[11px] font-bold sm:flex">
                    <span className="text-ha-success">+{stats.added}</span>
                    <span className="text-ha-danger">-{stats.removed}</span>
                  </span>
                ) : null}
              </div>

              {content && isDiff ? (
                <div className="flex shrink-0 items-center rounded-md border border-ha-border-strong bg-ha-panel p-0.5">
                  {(
                    [
                      { key: 'inline', label: '单栏' },
                      { key: 'split', label: '并排' },
                    ] as const
                  ).map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      aria-pressed={view === item.key}
                      onClick={() => setView(item.key)}
                      className={cn(
                        'inline-flex h-[22px] items-center rounded-[4px] px-2.5 text-[11px] transition-colors',
                        view === item.key
                          ? 'bg-[#1c1d1f] font-medium text-ha-text'
                          : 'text-ha-muted hover:text-ha-text-2',
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="min-h-0 flex-1 overflow-auto">
              {isImage ? (
                imageQuery.isPending ? (
                  <p className="p-6 text-[12px] text-ha-dim">读取图片…</p>
                ) : imageUrl && selected ? (
                  <div className="p-4">
                    <ArtifactPreview artifact={selected} src={imageUrl} />
                  </div>
                ) : (
                  <p className="p-6 text-[12px] text-ha-danger">
                    {imageQuery.error?.message ?? '图片读取失败。'}
                  </p>
                )
              ) : contentQuery.isPending ? (
                <p className="p-6 text-[12px] text-ha-dim">读取产物正文…</p>
              ) : content ? (
                isDiff ? (
                  <DiffViewer
                    content={content}
                    view={view}
                    numbers="double"
                    className="py-2"
                  />
                ) : selected ? (
                  <div className="p-4">
                    <ArtifactPreview
                      artifact={{
                        title: selected.title,
                        kind: selected.kind,
                        content,
                        filename: selected.filename,
                        content_type: selected.content_type,
                      }}
                    />
                  </div>
                ) : null
              ) : selected ? (
                <p className="p-6 text-[12px] text-ha-dim">
                  {isText
                    ? '产物正文为空。'
                    : '该文件不支持在线预览，请下载后查看。'}
                </p>
              ) : (
                <p className="p-6 text-[12px] text-ha-dim">
                  从左侧选择一个产物查看内容。
                </p>
              )}
            </div>

            <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-t border-ha-border px-4">
              <span className="flex min-w-0 items-center gap-2 font-mono text-[11px] text-ha-dim">
                <StatusDot tone={selected ? 'success' : 'neutral'} />
                <span className="truncate">
                  {selected
                    ? selected.run_id
                      ? '来自运行 #' + selected.run_id.slice(0, 8)
                      : '工作区产物'
                    : '未选择产物'}
                  {selected ? ' · ' + relativeTime(selected.created_at) : ''}
                  {content ? ' · ' + content.length + ' 字符' : ''}
                </span>
              </span>
              <div className="flex shrink-0 items-center gap-2">
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  onClick={copy}
                  disabled={!content}
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5 text-ha-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {copied ? '已复制' : '复制正文'}
                </ConsoleButton>
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  onClick={download}
                  disabled={!selected || downloadMutation.isPending}
                >
                  <Download className="h-3.5 w-3.5" />
                  下载
                </ConsoleButton>
              </div>
            </div>
          </ConsolePanel>
        </div>
      ) : null}
    </div>
  );
}
