/**
 * 本地工作区:目录列举、文本读取、文件写入与路径守卫。
 *
 * 安全边界是这个模块的核心职责:所有对外入口都必须先经过 resolveWithin,
 * 拒绝绝对路径、`..` 逃逸和「符号链接指向工作区外」三类越界。
 * 纯 Node 逻辑,不依赖 electron,工作区根目录由调用方传入。
 */
import { lstat, mkdir, open, readdir, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

import type { FileContent, FileEntry, WorkspaceInfo } from '@shared/contract';

/** 单次列目录最多返回的条目数,防止巨大目录拖垮渲染进程。 */
export const MAX_DIRECTORY_ENTRIES = 2000;

/** 单个文件最多读取的字节数;超出部分截断。 */
export const MAX_TEXT_FILE_BYTES = 256 * 1024;

/** 判断二进制文件时扫描的前缀字节数。 */
const BINARY_SNIFF_BYTES = 8 * 1024;

export type WorkspaceErrorCode =
  | 'invalid-path'
  | 'outside-root'
  | 'not-found'
  | 'not-directory'
  | 'not-file'
  | 'binary'
  | 'root-missing';

/** 工作区操作失败时抛出的可读错误(code 便于上层区分处理)。 */
export class WorkspaceError extends Error {
  readonly code: WorkspaceErrorCode;

  constructor(code: WorkspaceErrorCode, message: string) {
    super(message);
    this.name = 'WorkspaceError';
    this.code = code;
  }
}

function isPathWithin(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

/**
 * 校验并归一化一个「相对工作区根」的路径。
 * 兼容 Windows 分隔符,但对外统一使用 POSIX 风格(契约里的 FileEntry.path)。
 */
function normalizeRelativePath(relPath: string): string {
  if (typeof relPath !== 'string') {
    throw new WorkspaceError('invalid-path', '路径必须是字符串。');
  }
  if (relPath.includes('\0')) {
    throw new WorkspaceError('invalid-path', '路径包含非法字符。');
  }
  if (path.isAbsolute(relPath) || path.win32.isAbsolute(relPath)) {
    throw new WorkspaceError('invalid-path', '请使用相对工作区根目录的路径,不要使用绝对路径。');
  }
  const segments = relPath
    .replace(/\\/g, '/')
    .split('/')
    .filter((segment) => segment !== '' && segment !== '.');
  if (segments.includes('..')) {
    throw new WorkspaceError('outside-root', '路径不能通过 .. 跳出工作区目录。');
  }
  return segments.join('/');
}

/** 找到 target 自身或最近一个「已存在」的祖先,返回其真实路径(解析符号链接)。 */
async function realpathOfNearestExisting(target: string): Promise<string> {
  let current = target;
  for (;;) {
    try {
      return await realpath(current);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') throw error;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      throw new WorkspaceError('not-found', '无法定位工作区路径。');
    }
    current = parent;
  }
}

/**
 * 把相对路径解析为工作区内的绝对路径,越界一律抛错。
 *
 * 除字面量检查外,还会对「目标或最近的已存在祖先」做 realpath 前缀校验,
 * 因此 `root/link -> /etc` 这种符号链接逃逸也会被拒绝(即便目标尚不存在)。
 */
export async function resolveWithin(root: string, relPath = ''): Promise<string> {
  if (typeof root !== 'string' || !root.trim()) {
    throw new WorkspaceError('root-missing', '尚未选择工作区目录。');
  }
  const realRoot = await realpath(path.resolve(root)).catch(() => {
    throw new WorkspaceError('root-missing', '工作区目录不存在,请重新选择。');
  });
  const safeRel = normalizeRelativePath(relPath);
  const absolute = path.resolve(realRoot, safeRel);
  if (!isPathWithin(realRoot, absolute)) {
    throw new WorkspaceError('outside-root', '路径超出工作区目录范围。');
  }
  const resolvedExisting = await realpathOfNearestExisting(absolute);
  if (!isPathWithin(realRoot, resolvedExisting)) {
    throw new WorkspaceError('outside-root', '路径通过符号链接指向了工作区之外,已拒绝访问。');
  }
  return absolute;
}

/** 由工作区根目录生成界面展示信息。 */
export function describeWorkspace(root: string): WorkspaceInfo {
  const trimmed = root.trim();
  return {
    root: trimmed,
    name: path.basename(trimmed) || trimmed,
    openedAt: new Date().toISOString(),
  };
}

/** 把相对路径与条目名拼成 POSIX 风格路径。 */
function joinPosix(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

export interface ListDirectoryOptions {
  /** 测试/特殊场景可调低上限;默认 MAX_DIRECTORY_ENTRIES。 */
  maxEntries?: number;
}

/**
 * 列出目录项:目录在前、文件在后,同类按名称升序。
 * 结果最多 maxEntries 项(默认 2000),不做分页。
 */
export async function listDirectory(
  root: string,
  relPath = '',
  options: ListDirectoryOptions = {},
): Promise<FileEntry[]> {
  const safeRel = normalizeRelativePath(relPath);
  const absolute = await resolveWithin(root, safeRel);
  const info = await stat(absolute).catch(() => null);
  if (!info) throw new WorkspaceError('not-found', '目录不存在,可能已被删除。');
  if (!info.isDirectory()) {
    throw new WorkspaceError('not-directory', '该路径不是目录。');
  }
  const maxEntries = options.maxEntries ?? MAX_DIRECTORY_ENTRIES;

  const dirents = await readdir(absolute, { withFileTypes: true });
  const entries = await Promise.all(
    dirents.slice(0, Math.max(0, maxEntries)).map(async (dirent): Promise<FileEntry> => {
      const childPath = path.join(absolute, dirent.name);
      const linkInfo = await lstat(childPath).catch(() => null);
      let kind: FileEntry['kind'] = dirent.isDirectory() ? 'directory' : 'file';
      if (!dirent.isDirectory() && (dirent.isSymbolicLink() || linkInfo?.isSymbolicLink())) {
        // 符号链接按指向对象的类型归类(只用于展示,读取仍会再走一次守卫)。
        const targetInfo = await stat(childPath).catch(() => null);
        kind = targetInfo?.isDirectory() ? 'directory' : 'file';
      }
      return {
        path: joinPosix(safeRel, dirent.name),
        name: dirent.name,
        kind,
        size: kind === 'directory' ? 0 : (linkInfo?.size ?? 0),
        modifiedAt: (linkInfo?.mtime ?? new Date(0)).toISOString(),
      };
    }),
  );

  entries.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'directory' ? -1 : 1;
    return a.name.localeCompare(b.name, 'en', { numeric: true, sensitivity: 'base' });
  });
  return entries;
}

/** 判断读到的字节前缀是否像二进制文件(含 NUL 字节)。 */
function looksBinary(buffer: Buffer): boolean {
  const length = Math.min(buffer.length, BINARY_SNIFF_BYTES);
  for (let index = 0; index < length; index += 1) {
    if (buffer[index] === 0) return true;
  }
  return false;
}

/**
 * 读取工作区内的文本文件。
 * 超过 MAX_TEXT_FILE_BYTES 时只读前缀并标记 truncated;二进制文件抛出可读错误。
 */
export async function readTextFile(root: string, relPath: string): Promise<FileContent> {
  const safeRel = normalizeRelativePath(relPath);
  if (!safeRel) {
    throw new WorkspaceError('not-file', '请指定要读取的文件路径。');
  }
  const absolute = await resolveWithin(root, safeRel);
  const info = await stat(absolute).catch(() => null);
  if (!info) throw new WorkspaceError('not-found', '文件不存在,可能已被删除或移动。');
  if (info.isDirectory()) {
    throw new WorkspaceError('not-file', '这是一个目录,不能作为文件读取。');
  }

  const size = info.size;
  const readLength = Math.min(size, MAX_TEXT_FILE_BYTES);
  const handle = await open(absolute, 'r');
  try {
    const buffer = Buffer.alloc(readLength);
    const { bytesRead } = await handle.read(buffer, 0, readLength, 0);
    const data = buffer.subarray(0, bytesRead);
    if (looksBinary(data)) {
      throw new WorkspaceError('binary', '这是二进制文件,暂不支持在文本编辑器中打开。');
    }
    return {
      path: safeRel,
      content: data.toString('utf8'),
      size,
      truncated: size > MAX_TEXT_FILE_BYTES,
    };
  } finally {
    await handle.close();
  }
}

export interface WriteTextFileResult {
  path: string;
  bytes: number;
}

/** 写入工作区内的文本文件(自动创建父目录);路径守卫与读取一致。 */
export async function writeTextFile(
  root: string,
  relPath: string,
  content: string,
): Promise<WriteTextFileResult> {
  const safeRel = normalizeRelativePath(relPath);
  if (!safeRel) throw new WorkspaceError('invalid-path', '请指定要写入的文件路径。');
  const absolute = await resolveWithin(root, safeRel);
  await mkdir(path.dirname(absolute), { recursive: true });
  const payload = typeof content === 'string' ? content : String(content ?? '');
  await writeFile(absolute, payload, 'utf8');
  return { path: safeRel, bytes: Buffer.byteLength(payload, 'utf8') };
}

/** 工作区对象的可注入接口(agent 只依赖它,方便测试替身)。 */
export interface WorkspaceApi {
  listDirectory(root: string, relPath?: string): Promise<FileEntry[]>;
  readTextFile(root: string, relPath: string): Promise<FileContent>;
  writeTextFile(root: string, relPath: string, content: string): Promise<WriteTextFileResult>;
}

/** 真实实现,注入给 agent。 */
export const workspaceApi: WorkspaceApi = { listDirectory, readTextFile, writeTextFile };
