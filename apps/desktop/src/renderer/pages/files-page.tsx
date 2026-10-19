/**
 * 文件页:工作区目录树(懒加载) + 右侧文件预览。
 *
 * - 目录按需展开,列表与读取都走 workspace.list / workspace.read,主进程侧有
 *   路径逃逸与大小上限守卫,渲染进程只负责展示与可读提示;
 * - 预览用等宽字体,截断/二进制/读取失败都给出明确文案,不留白屏。
 */
import { Empty } from '@beauty/design';
import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileWarning,
  Folder,
  FolderOpen,
  RefreshCw,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import type { FileContent, FileEntry } from '@shared/contract';

import { useDesktop } from '@/lib/desktop-context';
import { baseName, errorMessage, formatBytes, looksBinary } from '@/lib/format';
import { cn } from '@/lib/utils';

import { ConsoleButton, Mono, ToneBadge } from '@/components/ui';

type DirState =
  | { status: 'loading' }
  | { status: 'ready'; entries: FileEntry[] }
  | { status: 'error'; message: string };

export function FilesPage() {
  const { state, actions } = useDesktop();
  const workspace = state.workspace;

  if (!workspace) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <Empty
          description="还没有选择本地工作区。选择目录后可以在这里浏览文件并预览内容。"
          actions={
            <ConsoleButton
              variant="primary"
              size="lg"
              onClick={() => void actions.chooseWorkspace()}
            >
              <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
              选择本地目录
            </ConsoleButton>
          }
        />
      </div>
    );
  }

  return (
    <FilesBrowser
      key={workspace.root}
      root={workspace.root}
      workspaceName={workspace.name || baseName(workspace.root)}
    />
  );
}

function FilesBrowser({
  root,
  workspaceName,
}: {
  root: string;
  workspaceName: string;
}) {
  const { bridge } = useDesktop();
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<FileContent | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const loadDir = useCallback(
    async (path: string) => {
      setDirs((previous) => ({ ...previous, [path]: { status: 'loading' } }));
      try {
        const entries = await bridge.workspace.list(path);
        setDirs((previous) => ({
          ...previous,
          [path]: { status: 'ready', entries },
        }));
      } catch (error) {
        setDirs((previous) => ({
          ...previous,
          [path]: { status: 'error', message: errorMessage(error) },
        }));
      }
    },
    [bridge],
  );

  useEffect(() => {
    void loadDir('');
  }, [loadDir]);

  const toggleDir = (path: string) => {
    const next = !(expanded[path] ?? false);
    setExpanded((previous) => ({ ...previous, [path]: next }));
    if (next && !dirs[path]) void loadDir(path);
  };

  const openFile = async (entry: FileEntry) => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setSelectedPath(entry.path);
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(true);
    try {
      const content = await bridge.workspace.read(entry.path);
      if (requestRef.current !== requestId) return;
      setPreview(content);
    } catch (error) {
      if (requestRef.current !== requestId) return;
      setPreviewError(errorMessage(error));
    } finally {
      if (requestRef.current === requestId) setPreviewLoading(false);
    }
  };

  const refresh = () => {
    setDirs({});
    setExpanded({});
    void loadDir('');
  };

  const renderEntries = (parentPath: string, depth: number): ReactNode => {
    const dir = dirs[parentPath];
    if (!dir) return null;
    if (dir.status === 'loading') {
      return (
        <p className="px-3 py-1.5 font-mono text-[11px] text-ha-faint">
          加载中…
        </p>
      );
    }
    if (dir.status === 'error') {
      return (
        <p className="px-3 py-1.5 text-[11px] leading-5 text-ha-danger">
          {dir.message}
        </p>
      );
    }
    if (dir.entries.length === 0) {
      return (
        <p className="px-3 py-1.5 font-mono text-[11px] text-ha-faint">
          空目录
        </p>
      );
    }

    return dir.entries.map((entry) => {
      const isDirectory = entry.kind === 'directory';
      const open = expanded[entry.path] ?? false;
      const selected = !isDirectory && selectedPath === entry.path;

      return (
        <div key={entry.path}>
          <button
            type="button"
            aria-expanded={isDirectory ? open : undefined}
            onClick={() =>
              isDirectory ? toggleDir(entry.path) : void openFile(entry)
            }
            className={cn(
              'flex w-full items-center gap-1.5 rounded px-2 py-1 text-left transition-colors',
              selected
                ? 'bg-ha-panel-2 text-ha-text'
                : 'text-ha-text-2 hover:bg-ha-panel-2/60',
            )}
            style={{ paddingLeft: 8 + depth * 14 }}
          >
            {isDirectory ? (
              open ? (
                <ChevronDown
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 text-ha-dim"
                />
              ) : (
                <ChevronRight
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 text-ha-dim"
                />
              )
            ) : (
              <FileIcon
                aria-hidden="true"
                className="h-3 w-3 shrink-0 text-ha-muted"
              />
            )}
            {isDirectory ? (
              <Folder
                aria-hidden="true"
                className="h-3.5 w-3.5 shrink-0 text-ha-accent-soft"
              />
            ) : null}
            <span className="truncate text-[12px]">{entry.name}</span>
            {!isDirectory ? (
              <span className="ml-auto shrink-0 pl-2 font-mono text-[10px] text-ha-faint">
                {formatBytes(entry.size)}
              </span>
            ) : null}
          </button>
          {isDirectory && open ? renderEntries(entry.path, depth + 1) : null}
        </div>
      );
    });
  };

  return (
    <div className="flex min-h-0 flex-1">
      {/* 目录树 */}
      <div className="flex w-[300px] shrink-0 flex-col border-r border-ha-border bg-ha-sidebar">
        <div className="flex h-9 shrink-0 items-center justify-between border-b border-ha-border px-3">
          <span className="text-[11px] font-semibold text-ha-dim">
            工作区文件
          </span>
          <button
            type="button"
            aria-label="刷新目录"
            title="刷新目录"
            onClick={refresh}
            className="grid h-6 w-6 place-items-center rounded text-ha-dim transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
          >
            <RefreshCw aria-hidden="true" className="h-3 w-3" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto py-2">
          {renderEntries('', 0)}
        </div>
        <div className="shrink-0 border-t border-ha-border px-3 py-2">
          <p
            className="truncate font-mono text-[10px] text-ha-faint"
            title={root}
          >
            {workspaceName} · {root}
          </p>
        </div>
      </div>

      {/* 预览 */}
      <div className="flex min-w-0 flex-1 flex-col bg-ha-bg">
        {selectedPath ? (
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-ha-border bg-ha-sidebar px-3">
            <FileIcon
              aria-hidden="true"
              className="h-3.5 w-3.5 shrink-0 text-ha-muted"
            />
            <nav
              aria-label="文件路径"
              className="flex min-w-0 items-center gap-1 font-mono text-[11px]"
            >
              <span className="shrink-0 text-ha-faint">{workspaceName}</span>
              {selectedPath.split('/').map((segment, index) => (
                <span
                  key={`${segment}-${index}`}
                  className="flex min-w-0 items-center gap-1"
                >
                  <ChevronRight
                    aria-hidden="true"
                    className="h-2.5 w-2.5 shrink-0 text-ha-ghost"
                  />
                  <span
                    className={cn(
                      'truncate',
                      index === selectedPath.split('/').length - 1
                        ? 'text-ha-text-2'
                        : 'text-ha-dim',
                    )}
                  >
                    {segment}
                  </span>
                </span>
              ))}
            </nav>
            <div className="ml-auto flex shrink-0 items-center gap-2">
              {preview?.truncated ? (
                <ToneBadge tone="warning">已截断</ToneBadge>
              ) : null}
              {preview ? (
                <Mono className="text-ha-faint">
                  {formatBytes(preview.size)}
                </Mono>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto">
          {!selectedPath ? (
            <div className="grid h-full place-items-center p-8">
              <Empty description="从左侧选择文件,在这里预览内容。" />
            </div>
          ) : previewLoading ? (
            <p className="px-4 py-3 font-mono text-[12px] text-ha-dim">
              读取中…
            </p>
          ) : previewError ? (
            <div className="p-4">
              <p className="flex items-start gap-2 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] leading-5 text-ha-danger">
                <FileWarning
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                />
                读取失败:{previewError}
              </p>
            </div>
          ) : preview ? (
            <div className="flex h-full flex-col">
              {preview.truncated ? (
                <p
                  role="status"
                  className="shrink-0 border-b border-ha-warning-border bg-ha-warning-bg px-4 py-2 text-[12px] leading-5 text-ha-warning"
                >
                  文件超过读取上限,下面只展示前 {preview.content.length}{' '}
                  个字符(完整大小 {formatBytes(preview.size)})。
                </p>
              ) : null}
              {looksBinary(preview.content) ? (
                <p className="px-4 py-3 text-[12px] leading-5 text-ha-muted">
                  这看起来是二进制文件,无法以文本方式预览。
                </p>
              ) : (
                <pre className="min-h-0 flex-1 overflow-auto whitespace-pre px-4 py-3 font-mono text-[12px] leading-5 text-ha-text-2">
                  {preview.content}
                </pre>
              )}
            </div>
          ) : (
            <p className="px-4 py-3 font-mono text-[12px] text-ha-dim">
              没有内容。
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
