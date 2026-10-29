/**
 * 文件树的增删行数汇总(纯函数):文件取自身,目录取整棵子树。
 *
 * 从 sandbox-file-tree 抽出,保持无 React 依赖,便于复用与单测。
 */
import type { FileLineStat } from '@/lib/tool-calls-api';

/** 汇总某个路径的增删行数:文件取自身,目录取整棵子树(按 'dir/' 前缀)。 */
export function lineChangesUnder(
  stats: ReadonlyMap<string, FileLineStat>,
  relative: string,
  isDir: boolean,
): FileLineStat | null {
  if (!isDir) return stats.get(relative) ?? null;
  const prefix = relative ? relative + '/' : '';
  let added = 0;
  let removed = 0;
  let hit = false;
  for (const [key, stat] of stats) {
    if (prefix === '' || key === relative || key.startsWith(prefix)) {
      added += stat.added;
      removed += stat.removed;
      hit = true;
    }
  }
  return hit ? { added, removed } : null;
}
