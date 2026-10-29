/**
 * 文件查看器「内容」页的读取 hook:按沙箱 + 路径拉一次只读文本(15s 内视为新鲜)。
 *
 * 抽出来让查看器外壳只订阅结果、内容页只负责渲染;查询键与刷新策略保持原样。
 * 内容页的渲染在 ./sandbox-file-viewer-content-pane。
 */
import { useQuery } from '@tanstack/react-query';

import { queryKeys } from '@/lib/query-keys';
import type { SandboxInfo } from '@/lib/sandbox-api';
import { readSandboxFile } from '@/lib/sandbox-api';

export function useSandboxFileContent(
  sandbox: SandboxInfo | null,
  path: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.sandboxFile(sandbox?.id ?? '', path),
    queryFn: () => readSandboxFile((sandbox as SandboxInfo).id, path),
    enabled: Boolean(sandbox) && enabled,
    staleTime: 15_000,
  });
}

/** 读取结果的查询句柄(内容页的入参类型)。 */
export type SandboxFileContentQuery = ReturnType<typeof useSandboxFileContent>;
