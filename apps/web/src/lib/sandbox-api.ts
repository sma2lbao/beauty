/**
 * 会话沙箱的数据层:workspace-service 的 /v1/sandboxes 契约。
 *
 * 会话页的「文件 / 终端」两个页签都靠这里:先按会话找沙箱(scope_key=conv:<id>),
 * 再列文件树、读文件、在沙箱里跑命令。环境变量与 workspace-api 一致:
 * `VITE_WORKSPACE_API_URL` 留空走同源(Vite dev 把 `/workspace-api` 代理到 :8005)。
 *
 * 后端字段是 snake_case,这里统一归一成 camelCase 给展示层;
 * 缺省字段一律降级成 null/空串,旧后端缺能力时页面能诚实地说"没有"。
 */
import { workspaceBase as BASE } from '@/config/env';
import { authMode } from '@/lib/auth';
import { createHttpClient } from '@/lib/http';

const PREFIX = authMode() === 'session' ? '/api/workspace' : '/workspace-api';
const SANDBOXES = `${PREFIX}/v1/sandboxes`;

/** 沙箱内文件树条目类型:未知/特殊文件统一 other,不猜。 */
export type SandboxFileKind = 'file' | 'dir' | 'symlink' | 'other';

/** 会话沙箱(只保留控制台用得到的字段)。 */
export type SandboxInfo = {
  id: string;
  name: string;
  status: string;
  /** 归属键:conv:<conversationId> / run:<runId>。 */
  scopeKey: string | null;
  workspaceId: string;
  runId: string | null;
  createdAt: string;
  lastUsedAt: string | null;
  expiresAt: string | null;
  lastError: string | null;
};

export type SandboxTreeEntry = {
  /** 沙箱内绝对路径(如 /workspace/src/a.ts)。 */
  path: string;
  name: string;
  kind: SandboxFileKind;
  sizeBytes: number | null;
  /** mtime(epoch 秒);取不到时为 null。 */
  modified: number | null;
};

export type SandboxTree = {
  path: string;
  entries: SandboxTreeEntry[];
  /** 目录条目数超上限被截断。 */
  truncated: boolean;
};

/** 读文件的展示形态:binary=true 时 text 为空串,不渲染正文。 */
export type SandboxFile = {
  path: string;
  sizeBytes: number;
  binary: boolean;
  text: string;
};

export type SandboxExecResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
  durationMs: number;
  truncated: boolean;
};

type RawSandbox = {
  id: string;
  name?: string;
  status?: string;
  scope_key?: string | null;
  workspace_id?: string;
  run_id?: string | null;
  created_at?: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  last_error?: string | null;
};

type RawTreeEntry = {
  path: string;
  name?: string;
  kind?: string;
  size_bytes?: number | null;
  modified?: number | null;
};

type RawTree = {
  path?: string;
  entries?: RawTreeEntry[];
  truncated?: boolean;
};

type RawFile = {
  path?: string;
  content_base64?: string;
  size_bytes?: number;
};

type RawExec = {
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  duration_ms?: number;
  truncated?: boolean;
};

const http = createHttpClient(BASE);

const FILE_KINDS = new Set<SandboxFileKind>([
  'file',
  'dir',
  'symlink',
  'other',
]);

function asKind(value: string | undefined): SandboxFileKind {
  return value && FILE_KINDS.has(value as SandboxFileKind)
    ? (value as SandboxFileKind)
    : 'other';
}

function asNumber(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function toSandboxInfo(raw: RawSandbox): SandboxInfo {
  return {
    id: raw.id,
    name: raw.name ?? raw.id,
    status: raw.status ?? 'unknown',
    scopeKey: raw.scope_key ?? null,
    workspaceId: raw.workspace_id ?? '',
    runId: raw.run_id ?? null,
    createdAt: raw.created_at ?? '',
    lastUsedAt: raw.last_used_at ?? null,
    expiresAt: raw.expires_at ?? null,
    lastError: raw.last_error ?? null,
  };
}

function toTreeEntry(raw: RawTreeEntry): SandboxTreeEntry {
  const path = raw.path ?? '';
  const name = raw.name || path.replace(/\/+$/, '').split('/').pop() || path;
  return {
    path,
    name,
    kind: asKind(raw.kind),
    sizeBytes: asNumber(raw.size_bytes),
    modified: asNumber(raw.modified),
  };
}

/** Base64 → 字节;非法 Base64 返回 null(调用方按二进制处理)。 */
function decodeBase64(value: string): Uint8Array | null {
  if (!value) return new Uint8Array(0);
  if (typeof atob !== 'function') return null;
  try {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  } catch {
    return null;
  }
}

/** 字节 → UTF-8 文本;含 NUL 或非法 UTF-8 视为二进制,返回 null。 */
function bytesToText(bytes: Uint8Array): string | null {
  if (bytes.includes(0)) return null;
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

/** 列沙箱;scopeKey 形如 conv:<id> / run:<id>(由后端按归属过滤)。 */
export async function listSandboxes(scopeKey?: string): Promise<SandboxInfo[]> {
  const query = scopeKey
    ? `?${new URLSearchParams({ scope_key: scopeKey }).toString()}`
    : '';
  const rows = await http.request<RawSandbox[]>(`${SANDBOXES}${query}`);
  return (rows ?? []).map(toSandboxInfo);
}

/**
 * 会话对应的运行中沙箱;没有就返回 null(会话还没跑过工具时是常态)。
 *
 * 后端已按 scope_key 过滤,这里再核对一次归属,避免旧后端忽略该参数时
 * 把别的会话的沙箱显示进来。
 */
export async function conversationSandbox(
  conversationId: string,
): Promise<SandboxInfo | null> {
  const scopeKey = `conv:${conversationId}`;
  const sandboxes = await listSandboxes(scopeKey);
  return (
    sandboxes.find(
      (sandbox) =>
        sandbox.scopeKey === scopeKey && sandbox.status === 'running',
    ) ?? null
  );
}

/** 列目录:entries 是扁平列表(含 depth 内各级嵌套项)。 */
export async function sandboxTree(
  sandboxId: string,
  options: { path?: string; depth?: number } = {},
): Promise<SandboxTree> {
  const query = new URLSearchParams({
    path: options.path ?? '/workspace',
    depth: String(options.depth ?? 1),
  });
  const raw = await http.request<RawTree>(
    `${SANDBOXES}/${encodeURIComponent(sandboxId)}/tree?${query.toString()}`,
  );
  return {
    path: raw.path ?? options.path ?? '/workspace',
    entries: (raw.entries ?? []).map(toTreeEntry),
    truncated: raw.truncated === true,
  };
}

/** 读沙箱内文件;text 已解码,二进制文件(含 NUL/非法 UTF-8)只给标记。 */
export async function readSandboxFile(
  sandboxId: string,
  path: string,
): Promise<SandboxFile> {
  const query = new URLSearchParams({ path });
  const raw = await http.request<RawFile>(
    `${SANDBOXES}/${encodeURIComponent(sandboxId)}/files?${query.toString()}`,
  );
  const bytes = decodeBase64(raw.content_base64 ?? '');
  const sizeBytes = raw.size_bytes ?? bytes?.length ?? 0;
  const text = bytes ? bytesToText(bytes) : null;
  if (text === null) {
    return {
      path: raw.path ?? path,
      sizeBytes,
      binary: true,
      text: '',
    };
  }
  return { path: raw.path ?? path, sizeBytes, binary: false, text };
}

/**
 * 在沙箱里执行一条命令(终端页签用)。
 *
 * 一律经 `sh -lc` 包一层:用户输入的是完整命令行(管道/重定向都该生效),
 * cwd 默认 /workspace;输出超限时后端置 truncated。
 */
export async function execSandbox(
  sandboxId: string,
  argv: readonly string[],
  cwd = '/workspace',
): Promise<SandboxExecResult> {
  const raw = await http.request<RawExec>(
    `${SANDBOXES}/${encodeURIComponent(sandboxId)}/exec`,
    {
      method: 'POST',
      body: JSON.stringify({ argv: [...argv], cwd }),
    },
  );
  return {
    exitCode: typeof raw.exit_code === 'number' ? raw.exit_code : -1,
    stdout: raw.stdout ?? '',
    stderr: raw.stderr ?? '',
    durationMs: raw.duration_ms ?? 0,
    truncated: raw.truncated === true,
  };
}

/** 持久 shell 会话句柄(session_id 用于 input/stream/close)。 */
export type SandboxSession = {
  sessionId: string;
  sandboxId: string;
  cwd: string;
  createdAt: string;
};

type RawSession = {
  session_id?: string;
  sandbox_id?: string;
  cwd?: string;
  created_at?: string;
};

const sessionPath = (sandboxId: string, sessionId: string) =>
  `${SANDBOXES}/${encodeURIComponent(sandboxId)}/sessions/${encodeURIComponent(sessionId)}`;

/**
 * 开一个持久 shell 会话(真 TTY)。
 *
 * 会话活到显式 close / 空闲超时 / 沙箱销毁,不随 HTTP 请求结束——
 * 终端页签据此在挂载时开一次,后续输入输出都挂在同一会话上。
 */
export async function openSandboxSession(
  sandboxId: string,
  options: { cwd?: string; cols?: number; rows?: number } = {},
): Promise<SandboxSession> {
  const body: Record<string, unknown> = {
    cols: options.cols ?? 120,
    rows: options.rows ?? 32,
  };
  if (options.cwd) body.cwd = options.cwd;
  const raw = await http.request<RawSession>(
    `${SANDBOXES}/${encodeURIComponent(sandboxId)}/sessions`,
    { method: 'POST', body: JSON.stringify(body) },
  );
  return {
    sessionId: raw.session_id ?? '',
    sandboxId: raw.sandbox_id ?? sandboxId,
    cwd: raw.cwd ?? '/workspace',
    createdAt: raw.created_at ?? '',
  };
}

/** 向会话 stdin 写文本:原样写入,不自动补换行(由调用方决定是否加 \n)。 */
export async function sendSandboxSessionInput(
  sandboxId: string,
  sessionId: string,
  data: string,
): Promise<void> {
  await http.request<void>(`${sessionPath(sandboxId, sessionId)}/input`, {
    method: 'POST',
    body: JSON.stringify({ data }),
  });
}

/** 关闭会话(幂等);终端页签在切换沙箱时尽力调用,卸载时交给空闲回收。 */
export async function closeSandboxSession(
  sandboxId: string,
  sessionId: string,
): Promise<void> {
  await http.request<void>(sessionPath(sandboxId, sessionId), {
    method: 'DELETE',
  });
}

/**
 * 会话输出流的完整地址(交给 fetch 读 SSE)。
 *
 * BFF 对以 `/stream` 结尾的路径做流式转发,所以这里必须带上 base 前缀;
 * 帧格式为 `id: <seq>` / `event: output|exit|error` / `data: <json>`,
 * 断线可用 Last-Event-ID 从上次 seq 之后续传。
 */
export function sandboxSessionStreamUrl(
  sandboxId: string,
  sessionId: string,
): string {
  return `${BASE}${sessionPath(sandboxId, sessionId)}/stream`;
}
