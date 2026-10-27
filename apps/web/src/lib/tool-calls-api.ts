/**
 * 会话级工具调用审计的数据层:后端 `GET /api/v1/conversations/{id}/tool-calls`。
 *
 * 与 run 事件流的关键差别:**这个端点跨 run 聚合、且不受事件单条 2000 字符的
 * 截断限制**,write/edit 直接回完整 unified diff 与增删行数。会话页的
 * 「改动」页签优先用它;端点降级(degraded,后端 tool-service 不可达)或失败时,
 * 展示层回退到 `@/lib/tool-patch` 从事件参数本地重建。
 *
 * 同源路径复用 runs/conversations 的 base(`@/config/env` 的 runsBase),
 * 鉴权/401 续期/错误映射全部走 `@/lib/http`。后端字段 snake_case,
 * 这里统一归一成 camelCase;缺省字段降级成 null/空数组,不猜。
 */
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';

import { runsBase as BASE } from '@/config/env';
import { createHttpClient } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import {
  normalizeSandboxPath,
  relativeSandboxPath,
  toSandboxPath,
} from '@/lib/sandbox-tree';

/** 一页的默认条数:跨 run 聚合通常几十条,200 足够覆盖一次会话。 */
export const TOOL_CALLS_PAGE_SIZE = 200;

/** 会产生文件补丁/行数统计的工具名。 */
const PATCH_TOOLS = new Set(['write', 'edit']);

/** 单条工具调用审计记录(展示层字段,全部 camelCase)。 */
export type ConversationToolCall = {
  id: string;
  toolCallId: string;
  runId: string | null;
  conversationId: string | null;
  toolName: string;
  toolVersion: string | null;
  risk: string | null;
  decision: string | null;
  ok: boolean;
  errorCode: string | null;
  durationMs: number | null;
  resultChars: number | null;
  bytesWritten: number | null;
  truncated: boolean;
  sha256Before: string | null;
  sha256After: string | null;
  /** 本次调用触碰的沙箱内路径(可能绝对、可能相对)。 */
  paths: string[];
  /** 完整 unified diff;只有 write/edit 才有,或后端降级时为空。 */
  patch: string | null;
  linesAdded: number | null;
  linesRemoved: number | null;
  /** 单条补丁本身超上限被后端截断(与事件截断无关)。 */
  patchTruncated: boolean;
  sandboxId: string | null;
  createdAt: string;
};

/** 分页信封;`degraded=true` 表示后端拿不到 tool-service,items 通常为空。 */
export type ConversationToolCallPage = {
  items: ConversationToolCall[];
  total: number;
  limit: number;
  offset: number;
  degraded: boolean;
};

/** 某个文件的一条补丁(服务端来源,已按时间升序)。 */
export type FilePatchItem = {
  toolName: string;
  /** 命中该文件的沙箱路径(能规范化时是绝对路径)。 */
  path: string;
  patch: string;
  linesAdded: number | null;
  linesRemoved: number | null;
  patchTruncated: boolean;
  createdAt: string;
};

export type FilePatchSet = {
  items: FilePatchItem[];
  /** 后端降级:调用方应回退到事件参数本地重建。 */
  degraded: boolean;
};

/** 一个文件的会话级增删行数合计。 */
export type FileLineStat = {
  added: number;
  removed: number;
};

type RawToolCall = {
  id?: string;
  tool_call_id?: string;
  run_id?: string | null;
  conversation_id?: string | null;
  tool_name?: string;
  tool_version?: string | null;
  risk?: string | null;
  decision?: string | null;
  ok?: boolean;
  error_code?: string | null;
  duration_ms?: number | null;
  result_chars?: number | null;
  bytes_written?: number | null;
  truncated?: boolean;
  sha256_before?: string | null;
  sha256_after?: string | null;
  paths?: unknown;
  patch?: string | null;
  lines_added?: number | null;
  lines_removed?: number | null;
  patch_truncated?: boolean;
  sandbox_id?: string | null;
  created_at?: string;
};

type RawPage = {
  items?: RawToolCall[];
  total?: number;
  limit?: number;
  offset?: number;
  degraded?: boolean;
};

const http = createHttpClient(BASE);

function asNumber(value: unknown): number | null {
  const numeric =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim()
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(numeric) ? numeric : null;
}

function asPaths(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is string => typeof item === 'string' && item.trim() !== '',
  );
}

function toToolCall(raw: RawToolCall): ConversationToolCall {
  return {
    id: raw.id ?? '',
    toolCallId: raw.tool_call_id ?? '',
    runId: raw.run_id ?? null,
    conversationId: raw.conversation_id ?? null,
    toolName: raw.tool_name ?? '',
    toolVersion: raw.tool_version ?? null,
    risk: raw.risk ?? null,
    decision: raw.decision ?? null,
    ok: raw.ok !== false,
    errorCode: raw.error_code ?? null,
    durationMs: asNumber(raw.duration_ms),
    resultChars: asNumber(raw.result_chars),
    bytesWritten: asNumber(raw.bytes_written),
    truncated: raw.truncated === true,
    sha256Before: raw.sha256_before ?? null,
    sha256After: raw.sha256_after ?? null,
    paths: asPaths(raw.paths),
    patch: typeof raw.patch === 'string' && raw.patch ? raw.patch : null,
    linesAdded: asNumber(raw.lines_added),
    linesRemoved: asNumber(raw.lines_removed),
    patchTruncated: raw.patch_truncated === true,
    sandboxId: raw.sandbox_id ?? null,
    createdAt: raw.created_at ?? '',
  };
}

/**
 * 拉会话的工具调用审计页(跨 run 聚合)。
 *
 * 非 2xx 抛 `@/lib/http` 的 ApiError;调用方(查看器/文件树)按"服务端补丁
 * 不可用"降级处理,不把错误直接抛给用户。
 */
export async function listConversationToolCalls(
  conversationId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<ConversationToolCallPage> {
  const limit = options.limit ?? TOOL_CALLS_PAGE_SIZE;
  const offset = options.offset ?? 0;
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const raw = await http.request<RawPage>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/tool-calls?${query.toString()}`,
  );
  return {
    items: (raw.items ?? []).map(toToolCall),
    total: asNumber(raw.total) ?? 0,
    limit: asNumber(raw.limit) ?? limit,
    offset: asNumber(raw.offset) ?? offset,
    degraded: raw.degraded === true,
  };
}

/**
 * 两个沙箱路径是否指向同一文件:绝对路径直接比,相对路径夹到 /workspace 再比。
 * 兼容后端回 `/workspace/src/a.ts`、工具参数里写 `src/a.ts` 两种形态。
 */
function sameSandboxPath(candidate: string, target: string): boolean {
  const left = normalizeSandboxPath(candidate) ?? toSandboxPath(candidate);
  const right = normalizeSandboxPath(target) ?? toSandboxPath(target);
  if (!left || !right) return false;
  if (left === right) return true;
  return relativeSandboxPath(left) === relativeSandboxPath(right);
}

/** created_at → 毫秒;缺时间/非法时间的排到末尾(排序稳定,不丢条目)。 */
function timeOf(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

/**
 * 从审计页里筛出针对某个文件的补丁,按 createdAt 升序。
 *
 * - 只认 write/edit、patch 非空、paths 命中目标的项;
 * - `degraded` 时一律返回空 items,让调用方无歧义地回退本地重建;
 * - 同一文件多次改写全部保留(演进的每一版都看得到)。
 */
export function filePatchesFromCalls(
  page: ConversationToolCallPage | undefined,
  filePath: string,
): FilePatchSet {
  if (!page || page.degraded) {
    return { items: [], degraded: page?.degraded === true };
  }
  const items: FilePatchItem[] = [];
  for (const call of page.items) {
    if (!PATCH_TOOLS.has(call.toolName.toLowerCase())) continue;
    if (!call.patch) continue;
    const matched = call.paths.find((path) => sameSandboxPath(path, filePath));
    if (!matched) continue;
    items.push({
      toolName: call.toolName,
      path: toSandboxPath(matched) ?? matched,
      patch: call.patch,
      linesAdded: call.linesAdded,
      linesRemoved: call.linesRemoved,
      patchTruncated: call.patchTruncated,
      createdAt: call.createdAt,
    });
  }
  items.sort((left, right) => timeOf(left.createdAt) - timeOf(right.createdAt));
  return { items, degraded: false };
}

/**
 * 按文件相对路径聚合本会话的增删行数(文件树用)。
 *
 * 只统计 write/edit 且 lines_added/lines_removed 至少有一项的调用;
 * 一条调用的 paths 全算(正常只有一条)。`degraded` 时返回空表。
 */
export function fileLineStats(
  page: ConversationToolCallPage | undefined,
): Map<string, FileLineStat> {
  const stats = new Map<string, FileLineStat>();
  if (!page || page.degraded) return stats;
  for (const call of page.items) {
    if (!PATCH_TOOLS.has(call.toolName.toLowerCase())) continue;
    if (call.linesAdded === null && call.linesRemoved === null) continue;
    const added = call.linesAdded ?? 0;
    const removed = call.linesRemoved ?? 0;
    for (const raw of call.paths) {
      const absolute = normalizeSandboxPath(raw) ?? toSandboxPath(raw);
      if (!absolute) continue;
      const key = relativeSandboxPath(absolute);
      const current = stats.get(key) ?? { added: 0, removed: 0 };
      current.added += added;
      current.removed += removed;
      stats.set(key, current);
    }
  }
  return stats;
}

/**
 * 会话工具调用查询:staleTime 10s,**不做全局轮询** —— 只有使用方(查看器
 * 「改动」页签 / 文件树可见时)显式传 `refetchInterval`,默认 false。
 * `conversationId` 缺省时查询禁用(不产生请求)。
 */
export function useConversationToolCalls(
  conversationId?: string,
  options: { enabled?: boolean; refetchInterval?: number | false } = {},
): UseQueryResult<ConversationToolCallPage> {
  return useQuery({
    queryKey: queryKeys.conversationToolCalls(conversationId ?? ''),
    queryFn: () => listConversationToolCalls(conversationId as string),
    enabled: Boolean(conversationId) && (options.enabled ?? true),
    staleTime: 10_000,
    refetchInterval: options.refetchInterval ?? false,
  });
}