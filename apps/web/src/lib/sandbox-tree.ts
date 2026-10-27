/**
 * 沙箱文件树的纯函数:扁平 entries → 分层树、depth 合并、展开态扁平化。
 *
 * 后端 tree 端点回的是 `depth` 内的扁平路径列表(不是嵌套 JSON),
 * 展开某个目录时再按该目录 depth=1 拉一次并合并。这里集中处理这些转换,
 * 组件只管渲染和请求。
 */
import type { SandboxTreeEntry } from '@/lib/sandbox-api';

export type SandboxTreeNode = {
  /** 沙箱内绝对路径。 */
  path: string;
  name: string;
  kind: SandboxTreeEntry['kind'];
  sizeBytes: number | null;
  modified: number | null;
  children: SandboxTreeNode[];
};

export type FlatSandboxTreeNode = {
  node: SandboxTreeNode;
  /** 缩进层级(顶层 0)。 */
  depth: number;
};

/** 沙箱工作目录根。 */
export const SANDBOX_ROOT = '/workspace';

/**
 * 规范化沙箱路径:必须以 / 开头且不含 '..';否则返回 null(不猜、不修)。
 * 末尾多余的 / 会去掉(根路径除外)。
 */
export function normalizeSandboxPath(path: unknown): string | null {
  if (typeof path !== 'string') return null;
  const trimmed = path.trim();
  if (!trimmed.startsWith('/')) return null;
  if (trimmed.split('/').includes('..')) return null;
  const normalized = trimmed.replace(/\/{2,}/g, '/');
  if (normalized === '/') return '/';
  return normalized.replace(/\/+$/, '');
}

/**
 * 绝对路径 → 相对 /workspace 的展示路径。
 * 不在 /workspace 下的路径原样返回(保留前导 / 以表达"在根之外")。
 */
export function relativeSandboxPath(
  path: string,
  root: string = SANDBOX_ROOT,
): string {
  if (path === root) return '';
  const prefix = root.endsWith('/') ? root : `${root}/`;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

/**
 * 把工具调用里的路径(可能是相对路径,如 src/a.ts)夹到 /workspace 下。
 * 用于打开文件查看器时读沙箱文件;非法路径返回 null。
 */
export function toSandboxPath(path: unknown): string | null {
  if (typeof path !== 'string') return null;
  const trimmed = path.trim();
  if (!trimmed) return null;
  const withRoot = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  const normalized = normalizeSandboxPath(withRoot);
  if (!normalized) return null;
  if (normalized === SANDBOX_ROOT || normalized.startsWith(`${SANDBOX_ROOT}/`)) {
    return normalized;
  }
  return normalized === '/' ? SANDBOX_ROOT : `${SANDBOX_ROOT}${normalized}`;
}

/** 父目录路径(根路径的父目录是它自己)。 */
export function parentSandboxPath(path: string): string {
  const index = path.lastIndexOf('/');
  if (index <= 0) return '/';
  return path.slice(0, index);
}

/** 目录 → 该目录下一级的直接父路径(用于判断触碰文件属于哪个目录)。 */
function isDirectoryEntry(entry: SandboxTreeEntry): boolean {
  return entry.kind === 'dir';
}

function kindOrder(kind: SandboxTreeNode['kind']): number {
  if (kind === 'dir') return 0;
  if (kind === 'file') return 1;
  return 2;
}

function sortNodes(nodes: SandboxTreeNode[]): void {
  nodes.sort((left, right) => {
    const byKind = kindOrder(left.kind) - kindOrder(right.kind);
    if (byKind !== 0) return byKind;
    return left.name.localeCompare(right.name);
  });
  for (const node of nodes) sortNodes(node.children);
}

/**
 * 扁平 entries → 分层树。
 *
 * 后端只保证 depth 内可见的项;某个目录的子项可能完全没出现,
 * 因此中途缺失的祖先补成 dir 节点(展开时再取真实内容)。
 */
export function fileTreeFrom(
  entries: readonly SandboxTreeEntry[],
): SandboxTreeNode[] {
  const nodes = new Map<string, SandboxTreeNode>();

  for (const entry of entries) {
    const path = normalizeSandboxPath(entry.path);
    if (!path || path === '/') continue;
    const pieces = path.split('/').filter((piece) => piece.length > 0);
    let current = '';
    pieces.forEach((piece, index) => {
      current = `${current}/${piece}`;
      const last = index === pieces.length - 1;
      const existing = nodes.get(current);
      const node: SandboxTreeNode = existing ?? {
        path: current,
        name: piece,
        kind: last ? entry.kind : 'dir',
        sizeBytes: null,
        modified: null,
        children: [],
      };
      if (last && !isDirectoryEntry(entry)) {
        node.kind = entry.kind;
        node.sizeBytes = entry.sizeBytes;
        node.modified = entry.modified;
      }
      if (!existing) nodes.set(current, node);
    });
  }

  const roots: SandboxTreeNode[] = [];
  const sorted = [...nodes.values()].sort((left, right) =>
    left.path.localeCompare(right.path),
  );
  for (const node of sorted) {
    const parentPath = parentSandboxPath(node.path);
    const parent = nodes.get(parentPath);
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  sortNodes(roots);
  return roots;
}

/** 在层级树里找某个绝对路径的节点。 */
function findTreeNode(
  nodes: readonly SandboxTreeNode[],
  path: string,
): SandboxTreeNode | null {
  for (const node of nodes) {
    if (node.path === path) return node;
    const hit = findTreeNode(node.children, path);
    if (hit) return hit;
  }
  return null;
}

/**
 * 渲染某个目录的直接子项:fileTreeFrom 会建出 /workspace 这一层,
 * 而界面要的是它的内容。根节点不存在时退回整棵树(不丢信息)。
 */
export function fileTreeUnder(
  entries: readonly SandboxTreeEntry[],
  root: string = SANDBOX_ROOT,
): SandboxTreeNode[] {
  const roots = fileTreeFrom(entries);
  const target = normalizeSandboxPath(root);
  if (!target) return roots;
  return findTreeNode(roots, target)?.children ?? roots;
}

/**
 * 合并两次 tree 结果(展开目录时用)。
 *
 * entries 可能重叠(同级已加载过的项),按 path 去重、incoming 覆盖 current;
 * 保持先后顺序稳定,避免合并瞬间树跳位。
 */
export function mergeTreeEntries(
  current: readonly SandboxTreeEntry[],
  incoming: readonly SandboxTreeEntry[],
): SandboxTreeEntry[] {
  const merged = new Map<string, SandboxTreeEntry>();
  for (const entry of current) merged.set(entry.path, entry);
  for (const entry of incoming) merged.set(entry.path, entry);
  return [...merged.values()];
}

/** 按展开态把树拍平(只含可见行),供渲染列表用。 */
export function flattenTree(
  nodes: readonly SandboxTreeNode[],
  expanded: ReadonlySet<string>,
  depth = 0,
): FlatSandboxTreeNode[] {
  const rows: FlatSandboxTreeNode[] = [];
  for (const node of nodes) {
    rows.push({ node, depth });
    if (node.kind === 'dir' && expanded.has(node.path)) {
      rows.push(...flattenTree(node.children, expanded, depth + 1));
    }
  }
  return rows;
}

/**
 * 工具调用触碰过的文件集合(相对路径)。
 *
 * 只收集能安全规范化的路径;目录不展开,树组件自己用前缀判断。
 */
export function touchedPathSet(
  paths: readonly string[] | undefined,
): Set<string> {
  const touched = new Set<string>();
  for (const raw of paths ?? []) {
    const path = toSandboxPath(raw);
    if (!path) continue;
    touched.add(relativeSandboxPath(path));
  }
  return touched;
}

/** 某个目录下是否有触碰过的文件(用于目录高亮)。 */
export function hasTouchedUnder(
  touched: ReadonlySet<string>,
  dirPath: string,
): boolean {
  const relative = relativeSandboxPath(dirPath);
  if (!relative) return touched.size > 0;
  const prefix = `${relative}/`;
  for (const path of touched) {
    if (path.startsWith(prefix)) return true;
  }
  return false;
}
