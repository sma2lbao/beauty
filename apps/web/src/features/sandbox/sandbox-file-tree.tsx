/**
 * 执行面板「文件」页签:会话沙箱的工作区文件树。
 *
 * 数据来自 workspace-service 的 tree 端点(扁平 entries)。首屏按 depth=3
 * 一次拉三级;更深的目录在展开时按该目录 depth=1 再取一层(懒加载分支),
 * 这样既不用一次拉全树,也不会展开时整棵树重新请求。
 *
 * 工具调用触碰过的文件会带一个强调点:用户从「步骤」里看到写了哪个文件,
 * 在文件树里能对上。
 *
 * 传了 conversationId 时还会叠加本会话的增删行数(来自会话级 tool-calls 审计):
 * 文件行在大小右侧显示 +N/−M,目录行显示其子树汇总。端点降级或没传会话 id 时
 * 保持原来的纯文件树。
 */
import {
  File,
  FileQuestion,
  Folder,
  FolderOpen,
  Link2,
  RefreshCw,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { ConsoleButton } from '@/components/console/ui';
import { formatBytes } from '@/lib/presentation';
import { queryKeys } from '@/lib/query-keys';
import {
  fileTreeUnder,
  hasTouchedUnder,
  relativeSandboxPath,
  touchedPathSet,
  type SandboxTreeNode,
} from '@/lib/sandbox-tree';
import { sandboxTree } from '@/lib/sandbox-api';
import {
  fileLineStats,
  useConversationToolCalls,
  type FileLineStat,
} from '@/lib/tool-calls-api';
import { cn } from '@/lib/utils';

import { lineChangesUnder } from './sandbox-tree-stats';

/** 首屏拉取的层级:够看到 src/ 下的常见结构,又不至于一次拉满整棵树。 */
const INITIAL_DEPTH = 3;
const ROOT = '/workspace';

/** 查看器/文件树可见时的统计刷新间隔:Agent 继续写文件时要能更新。 */
const STATS_REFETCH_MS = 15_000;

type TreeSharedProps = {
  sandboxId: string;
  expanded: ReadonlySet<string>;
  selectedPath: string | null;
  touched: ReadonlySet<string>;
  /** 相对路径 → 本会话增删行数;没有会话 id 或端点降级时为 null。 */
  stats: ReadonlyMap<string, FileLineStat> | null;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
};

export function SandboxFileTree({
  sandboxId,
  touchedPaths,
  selectedPath,
  conversationId,
  onSelect,
}: {
  sandboxId: string;
  /** 本会话工具调用触碰过的路径(tool-meta 的 paths)。 */
  touchedPaths?: readonly string[];
  selectedPath?: string | null;
  /** 会话 id:有值时叠加本会话增删行数;缺省时不请求,行为与旧版一致。 */
  conversationId?: string;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const queryClient = useQueryClient();
  const touched = useMemo(() => touchedPathSet(touchedPaths), [touchedPaths]);

  // 统计只做展示,拿不到(未传 id / 降级 / 失败)就当没有,不打扰用户。
  const toolCalls = useConversationToolCalls(conversationId, {
    refetchInterval: STATS_REFETCH_MS,
  });
  const stats = useMemo(() => {
    if (!conversationId || toolCalls.data?.degraded) return null;
    if (!toolCalls.data) return null;
    const map = fileLineStats(toolCalls.data);
    return map.size > 0 ? map : null;
  }, [conversationId, toolCalls.data]);

  const tree = useQuery({
    queryKey: queryKeys.sandboxTree(sandboxId, ROOT, INITIAL_DEPTH),
    queryFn: () => sandboxTree(sandboxId, { path: ROOT, depth: INITIAL_DEPTH }),
    staleTime: 10_000,
  });

  const nodes = useMemo(
    () => (tree.data ? fileTreeUnder(tree.data.entries) : []),
    [tree.data],
  );

  function toggle(path: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['sandbox-tree'] });
  }

  const shared: TreeSharedProps = {
    sandboxId,
    expanded,
    selectedPath: selectedPath ?? null,
    touched,
    stats,
    onToggle: toggle,
    onSelect,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between px-0.5 pb-2">
        <span className="text-[11px] font-semibold text-ha-dim">
          工作区文件
          <span className="ml-1.5 font-mono font-normal text-ha-faint">
            /workspace
          </span>
        </span>
        <button
          type="button"
          aria-label="刷新文件树"
          onClick={refresh}
          className="grid h-6 w-6 place-items-center rounded text-ha-faint transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2"
        >
          <RefreshCw
            aria-hidden="true"
            className={cn('h-3.5 w-3.5', tree.isFetching && 'animate-spin')}
          />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-ha-border bg-ha-panel/60 p-1">
        {tree.isPending ? (
          <div className="space-y-1.5 p-2">
            {[0, 1, 2, 3, 4].map((index) => (
              <div
                key={index}
                className="h-5 animate-pulse rounded bg-ha-panel-2"
                style={{ width: `${88 - index * 9}%` }}
              />
            ))}
          </div>
        ) : tree.isError ? (
          <div className="space-y-2 p-3 text-center">
            <p className="text-[12px] text-ha-danger">
              {tree.error instanceof Error
                ? tree.error.message
                : '文件树加载失败'}
            </p>
            <ConsoleButton
              size="sm"
              variant="secondary"
              onClick={() => tree.refetch()}
            >
              重试
            </ConsoleButton>
          </div>
        ) : nodes.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] leading-5 text-ha-dim">
            沙箱工作区还是空的。Agent 写出文件后会出现在这里。
          </p>
        ) : (
          <TreeLevel nodes={nodes} level={0} shared={shared} />
        )}
      </div>

      {tree.data?.truncated ? (
        <p className="shrink-0 px-1 pt-1.5 text-[11px] text-ha-warning">
          条目过多，已截断显示。
        </p>
      ) : null}
    </div>
  );
}

/** 一层节点(来自任意一次 tree 查询结果);子级由 TreeRow 递归展开。 */
function TreeLevel({
  nodes,
  level,
  shared,
}: {
  nodes: readonly SandboxTreeNode[];
  level: number;
  shared: TreeSharedProps;
}) {
  return (
    <ul className="py-0.5">
      {nodes.map((node) => (
        <TreeRow key={node.path} node={node} depth={level} shared={shared} />
      ))}
    </ul>
  );
}

function TreeRow({
  node,
  depth,
  shared,
}: {
  node: SandboxTreeNode;
  depth: number;
  shared: TreeSharedProps;
}) {
  const isDir = node.kind === 'dir';
  const open = isDir && shared.expanded.has(node.path);
  const selected = shared.selectedPath === node.path;
  const relative = relativeSandboxPath(node.path);
  const marked = isDir
    ? hasTouchedUnder(shared.touched, node.path)
    : shared.touched.has(relative);
  const size =
    !isDir && node.sizeBytes !== null ? formatBytes(node.sizeBytes) : null;
  const changes = shared.stats
    ? lineChangesUnder(shared.stats, relative, isDir)
    : null;
  const added = changes && changes.added > 0 ? changes.added : 0;
  const removed = changes && changes.removed > 0 ? changes.removed : 0;
  const title =
    added > 0 || removed > 0
      ? '本会话 +' + String(added) + ' −' + String(removed) + ' 行'
      : relative || node.path;

  return (
    <li>
      <button
        type="button"
        onClick={() =>
          isDir ? shared.onToggle(node.path) : shared.onSelect(node.path)
        }
        title={title}
        aria-expanded={isDir ? open : undefined}
        className={cn(
          'flex w-full items-center gap-1.5 rounded px-1.5 py-[3px] text-left font-mono text-[12px] leading-5 transition-colors',
          selected
            ? 'bg-ha-accent-soft text-ha-text'
            : 'text-ha-text-2 hover:bg-ha-panel-2',
        )}
        style={{ paddingLeft: 6 + depth * 14 }}
      >
        <span className="shrink-0">
          <TreeIcon kind={node.kind} open={open} />
        </span>
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        {marked ? (
          <span
            aria-label="本会话工具调用触碰过"
            title="本会话工具调用触碰过"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-ha-accent"
          />
        ) : null}
        {size ? (
          <span className="shrink-0 text-[10px] text-ha-faint">{size}</span>
        ) : null}
        {added > 0 || removed > 0 ? (
          <span className="shrink-0 font-mono text-[10px]">
            {added > 0 ? (
              <span className="text-ha-success">{'+' + String(added)}</span>
            ) : null}
            {removed > 0 ? (
              <span className="text-ha-danger">{'−' + String(removed)}</span>
            ) : null}
          </span>
        ) : null}
      </button>
      {isDir && open ? (
        node.children.length > 0 ? (
          <TreeLevel nodes={node.children} level={depth + 1} shared={shared} />
        ) : (
          <LazyBranch path={node.path} level={depth + 1} shared={shared} />
        )
      ) : null}
    </li>
  );
}

/** 展开一个还没加载子项的目录:按该目录 depth=1 现取一层。 */
function LazyBranch({
  path,
  level,
  shared,
}: {
  path: string;
  level: number;
  shared: TreeSharedProps;
}) {
  const query = useQuery({
    queryKey: queryKeys.sandboxTree(shared.sandboxId, path, 1),
    queryFn: () => sandboxTree(shared.sandboxId, { path, depth: 1 }),
    staleTime: 10_000,
  });
  const nodes = useMemo(
    () => (query.data ? fileTreeUnder(query.data.entries, path) : []),
    [query.data, path],
  );

  if (query.isPending) {
    return (
      <p
        style={{ paddingLeft: 24 + level * 14 }}
        className="py-1 text-[11px] text-ha-faint"
      >
        加载中…
      </p>
    );
  }
  if (query.isError) {
    return (
      <p
        style={{ paddingLeft: 24 + level * 14 }}
        className="py-1 text-[11px] text-ha-danger"
      >
        目录读取失败
        <button
          type="button"
          onClick={() => query.refetch()}
          className="ml-1.5 underline decoration-dotted hover:text-ha-text-2"
        >
          重试
        </button>
      </p>
    );
  }
  if (nodes.length === 0) {
    return (
      <p
        style={{ paddingLeft: 24 + level * 14 }}
        className="py-1 text-[11px] text-ha-faint"
      >
        空目录
      </p>
    );
  }
  return <TreeLevel nodes={nodes} level={level} shared={shared} />;
}

function TreeIcon({
  kind,
  open,
}: {
  kind: SandboxTreeNode['kind'];
  open: boolean;
}) {
  if (kind === 'dir') {
    return open ? (
      <FolderOpen aria-hidden="true" className="h-3.5 w-3.5 text-ha-warning" />
    ) : (
      <Folder aria-hidden="true" className="h-3.5 w-3.5 text-ha-warning" />
    );
  }
  if (kind === 'symlink') {
    return <Link2 aria-hidden="true" className="h-3.5 w-3.5 text-ha-info" />;
  }
  if (kind === 'other') {
    return (
      <FileQuestion aria-hidden="true" className="h-3.5 w-3.5 text-ha-faint" />
    );
  }
  return <File aria-hidden="true" className="h-3.5 w-3.5 text-ha-muted" />;
}
