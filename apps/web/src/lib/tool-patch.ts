/**
 * 从工具调用参数重建补丁(纯前端)。
 *
 * 后端 run.tool.called.v1 的 detail 只有 200 字符摘要,write/edit 的结果里
 * 也没有 unified diff,所以「改动」页签只能用参数还原:
 * write → 整文件内容全是新增行;edit → old_string 删、new_string 增。
 *
 * 参数来自事件,单条最多 2000 字符,可能被截断——解析失败时如实提示,
 * 不假装补丁完整。
 */
import type { ToolCall } from '@/lib/presentation/tools';
import { normalizeSandboxPath, relativeSandboxPath } from '@/lib/sandbox-tree';

/** 参数 JSON 解析失败时的固定文案(参数被事件截断是最常见原因)。 */
export const PATCH_TRUNCATED_NOTICE = '参数被截断，无法重建补丁';

export type SynthesizedPatch = {
  seq: number;
  toolName: string;
  /** 沙箱内绝对路径(能规范化时)或参数里的原始路径。 */
  path: string;
  /** 可直接喂 parseUnifiedDiff 的补丁文本。 */
  diff: string;
  /** 附注:空文件、替换全部匹配等。 */
  note: string | null;
};

export type TruncatedPatch = {
  seq: number;
  toolName: string;
  /** 原始参数文本,供展开查看。 */
  raw: string;
};

export type FilePatch =
  | { status: 'ok'; patches: SynthesizedPatch[] }
  | { status: 'truncated'; items: TruncatedPatch[] }
  | { status: 'none' };

type PatchArguments = {
  path?: unknown;
  content?: unknown;
  old_string?: unknown;
  new_string?: unknown;
  replace_all?: unknown;
};

function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split('\n');
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function hunkHeader(oldCount: number, newCount: number): string {
  const oldStart = oldCount > 0 ? 1 : 0;
  const newStart = newCount > 0 ? 1 : 0;
  return `@@ -${oldStart},${oldCount} +${newStart},${newCount} @@`;
}

function diffHeader(path: string): string {
  const label = relativeSandboxPath(path);
  return `--- a/${label}\n+++ b/${label}`;
}

/** 参数里的路径是否指向目标文件(兼容 /workspace 绝对路径与相对路径)。 */
function samePath(candidate: unknown, target: string): boolean {
  if (typeof candidate !== 'string' || candidate.length === 0) return false;
  const left = normalizeSandboxPath(candidate);
  const right = normalizeSandboxPath(target);
  if (left && right && left === right) return true;
  const leftRelative = left ? relativeSandboxPath(left) : candidate;
  const rightRelative = right ? relativeSandboxPath(right) : target;
  return leftRelative === rightRelative;
}

function buildWritePatch(
  call: ToolCall,
  args: PatchArguments,
  target: string,
): SynthesizedPatch {
  const content = typeof args.content === 'string' ? args.content : '';
  const lines = splitLines(content);
  const body = lines.map((line) => `+${line}`);
  const path =
    typeof args.path === 'string' && normalizeSandboxPath(args.path)
      ? args.path
      : target;
  return {
    seq: call.seq,
    toolName: call.name,
    path,
    diff: [diffHeader(path), hunkHeader(0, lines.length), ...body].join('\n'),
    note: lines.length === 0 ? '写入空文件' : null,
  };
}

function buildEditPatch(
  call: ToolCall,
  args: PatchArguments,
  target: string,
): SynthesizedPatch {
  const oldText = typeof args.old_string === 'string' ? args.old_string : '';
  const newText = typeof args.new_string === 'string' ? args.new_string : '';
  const oldLines = splitLines(oldText);
  const newLines = splitLines(newText);
  const path =
    typeof args.path === 'string' && normalizeSandboxPath(args.path)
      ? args.path
      : target;
  const body = [
    ...oldLines.map((line) => `-${line}`),
    ...newLines.map((line) => `+${line}`),
  ];
  const notes: string[] = [];
  if (args.replace_all === true) notes.push('替换全部匹配');
  if (oldLines.length === 0) notes.push('新增内容');
  if (newLines.length === 0) notes.push('删除内容');
  return {
    seq: call.seq,
    toolName: call.name,
    path,
    diff: [
      diffHeader(path),
      hunkHeader(oldLines.length, newLines.length),
      ...body,
    ].join('\n'),
    note: notes.length > 0 ? notes.join(' · ') : null,
  };
}

/**
 * 筛出针对某个文件的、可重建补丁的工具调用。
 *
 * - write/edit 之外的工具一律忽略(它们不产生可展示的整文件改动);
 * - 按 seq 升序(事件顺序)排列,同一文件多次改写时能看到完整过程;
 * - 有任何一条 write/edit 参数被截断就整体降级为 truncated,连同原始文本返回。
 */
export function filePatchFromCalls(
  calls: readonly ToolCall[],
  filePath: string,
): FilePatch {
  const patches: SynthesizedPatch[] = [];
  const truncated: TruncatedPatch[] = [];

  for (const call of [...calls].sort((left, right) => left.seq - right.seq)) {
    if (call.name !== 'write' && call.name !== 'edit') continue;
    let args: PatchArguments;
    try {
      const parsed: unknown = JSON.parse(call.arguments);
      if (!parsed || typeof parsed !== 'object') continue;
      args = parsed as PatchArguments;
    } catch {
      truncated.push({
        seq: call.seq,
        toolName: call.name,
        raw: call.arguments,
      });
      continue;
    }
    if (!samePath(args.path, filePath)) continue;
    patches.push(
      call.name === 'write'
        ? buildWritePatch(call, args, filePath)
        : buildEditPatch(call, args, filePath),
    );
  }

  if (truncated.length > 0) return { status: 'truncated', items: truncated };
  if (patches.length === 0) return { status: 'none' };
  return { status: 'ok', patches };
}
