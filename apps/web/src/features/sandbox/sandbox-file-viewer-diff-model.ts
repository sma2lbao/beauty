/**
 * 文件查看器「改动」页的数据模型:把服务端审计补丁与本地工具参数重建统一成
 * DiffSection,再归并成可直接渲染的 DiffView(完整 / 截断 / 无记录)。
 *
 * 纯函数、不依赖 React;渲染在 ./sandbox-file-viewer-diff-pane。
 */
import type { ToolCall } from '@/lib/presentation';
import type {
  ConversationToolCallPage,
  FilePatchItem,
} from '@/lib/tool-calls-api';
import { filePatchesFromCalls } from '@/lib/tool-calls-api';
import type { TruncatedPatch } from '@/lib/tool-patch';
import { filePatchFromCalls } from '@/lib/tool-patch';

/** 补丁展示的一节(服务端与本地来源统一成同一形状)。 */
export type DiffSection = {
  key: string;
  /** 事件/工具调用的 seq;服务端来源没有 seq(null)。 */
  seq: number | null;
  toolName: string;
  path: string;
  diff: string;
  note: string | null;
  linesAdded: number | null;
  linesRemoved: number | null;
  patchTruncated: boolean;
  createdAt: string | null;
};

export type DiffView =
  | { status: 'ok'; source: 'server' | 'local'; sections: DiffSection[] }
  | { status: 'truncated'; items: TruncatedPatch[] }
  | { status: 'none' };

/** ISO 时间 → 本地「月/日 时:分」;非法/缺失时间原样返回,不显示 Invalid Date。 */
export function formatPatchStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/**
 * 解析该文件该看哪些补丁:服务端优先,不可用时回退本地重建。
 *
 * - 服务端(degraded=false)有命中项 → 完整 diff;
 * - degraded / 端点失败 / 无命中 → 本地重建,截断时保留原有提示;
 * - 两者都没有 → none。
 */
export function buildDiffView(
  server: ConversationToolCallPage | undefined,
  toolCalls: readonly ToolCall[],
  path: string,
): DiffView {
  const remote: FilePatchItem[] = filePatchesFromCalls(server, path).items;
  if (remote.length > 0) {
    return {
      status: 'ok',
      source: 'server',
      sections: remote.map((item, index) => ({
        key: 'server-' + String(index),
        seq: null,
        toolName: item.toolName,
        path: item.path,
        diff: item.patch,
        note: null,
        linesAdded: item.linesAdded,
        linesRemoved: item.linesRemoved,
        patchTruncated: item.patchTruncated,
        createdAt: item.createdAt || null,
      })),
    };
  }

  const local = filePatchFromCalls(toolCalls, path);
  if (local.status === 'ok') {
    return {
      status: 'ok',
      source: 'local',
      sections: local.patches.map((item) => ({
        key: 'local-' + String(item.seq),
        seq: item.seq,
        toolName: item.toolName,
        path: item.path,
        diff: item.diff,
        note: item.note,
        linesAdded: null,
        linesRemoved: null,
        patchTruncated: false,
        createdAt: item.occurredAt ?? null,
      })),
    };
  }
  if (local.status === 'truncated') {
    return { status: 'truncated', items: local.items };
  }
  return { status: 'none' };
}
