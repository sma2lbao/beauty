/**
 * 本地会话存储(userData/sessions/<id>.json)。
 *
 * 每个会话一个 JSON 文件,天然避免多进程/多窗口同时写同一个大文件;
 * 列表接口只返回摘要,不把消息体拖进渲染进程。
 * 同一会话的写操作用 Promise 队列串行化,防止「读-改-写」互相覆盖。
 */
import { randomUUID } from 'node:crypto';
import { mkdir, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

import type { ChatMessage, ChatRole, LocalSession, SessionSummary } from '@shared/contract';

/** 会话 id 只允许 URL 安全字符,同时挡住 `../` 之类的路径穿越。 */
const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

/** 新建会话的默认标题。 */
export const DEFAULT_SESSION_TITLE = '新会话';

export interface CreateSessionInput {
  title?: string;
  /** 会话创建时绑定的工作区;之后切换目录不影响已存在的会话。 */
  workspaceRoot?: string | null;
}

export interface AppendMessageInput {
  role: ChatRole;
  content: string;
  toolName?: string;
  toolStatus?: ChatMessage['toolStatus'];
}

export interface AppendMessageResult {
  session: LocalSession;
  message: ChatMessage;
}

/** 消息的可更新字段(角色与 id 不可改)。 */
export type ChatMessagePatch = Partial<Pick<ChatMessage, 'content' | 'toolStatus' | 'toolName'>>;

function assertSessionId(id: unknown): string {
  if (typeof id !== 'string' || !SESSION_ID_PATTERN.test(id)) {
    throw new Error('会话 id 不合法。');
  }
  return id;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function normalizeIso(value: unknown, fallback: string): string {
  if (typeof value === 'string') {
    const timestamp = Date.parse(value);
    if (!Number.isNaN(timestamp)) return new Date(timestamp).toISOString();
  }
  return fallback;
}

function normalizeMessage(value: unknown): ChatMessage | null {
  if (!isRecord(value)) return null;
  const role = value.role;
  if (role !== 'user' && role !== 'assistant' && role !== 'tool') return null;
  if (typeof value.id !== 'string' || !value.id) return null;
  if (typeof value.content !== 'string') return null;
  const message: ChatMessage = {
    id: value.id,
    role,
    content: value.content,
    createdAt: normalizeIso(value.createdAt, new Date(0).toISOString()),
  };
  if (typeof value.toolName === 'string') message.toolName = value.toolName;
  const status = value.toolStatus;
  if (status === 'running' || status === 'ok' || status === 'error') message.toolStatus = status;
  return message;
}

/** 把磁盘上的原始数据规整成 LocalSession;损坏到无法识别时返回 null。 */
function normalizeSession(value: unknown): LocalSession | null {
  if (!isRecord(value)) return null;
  if (typeof value.id !== 'string' || !SESSION_ID_PATTERN.test(value.id)) return null;
  const nowIso = new Date().toISOString();
  const createdAt = normalizeIso(value.createdAt, nowIso);
  const messages = Array.isArray(value.messages)
    ? value.messages.map(normalizeMessage).filter((item): item is ChatMessage => item !== null)
    : [];
  return {
    id: value.id,
    title: typeof value.title === 'string' && value.title.trim() ? value.title.trim() : DEFAULT_SESSION_TITLE,
    workspaceRoot: typeof value.workspaceRoot === 'string' ? value.workspaceRoot : null,
    createdAt,
    updatedAt: normalizeIso(value.updatedAt, createdAt),
    messageCount: messages.length,
    messages,
  };
}

function toSummary(session: LocalSession): SessionSummary {
  return {
    id: session.id,
    title: session.title,
    workspaceRoot: session.workspaceRoot,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messageCount,
  };
}

/** 生成严格递增的时间戳,避免同一毫秒内两次写入导致排序不稳定。 */
function nextTimestamp(previous: string | undefined): string {
  const now = Date.now();
  const previousMs = previous ? Date.parse(previous) : Number.NaN;
  const nextMs = Number.isNaN(previousMs) ? now : Math.max(now, previousMs + 1);
  return new Date(nextMs).toISOString();
}

function createChatMessage(input: AppendMessageInput, createdAt: string): ChatMessage {
  const message: ChatMessage = {
    id: randomUUID(),
    role: input.role,
    content: input.content,
    createdAt,
  };
  if (input.toolName) message.toolName = input.toolName;
  if (input.toolStatus) message.toolStatus = input.toolStatus;
  return message;
}

export class SessionStore {
  readonly directory: string;

  private readonly locks = new Map<string, Promise<unknown>>();

  constructor(directory: string) {
    this.directory = directory;
  }

  private filePath(id: string): string {
    return path.join(this.directory, `${assertSessionId(id)}.json`);
  }

  /** 同一会话的读-改-写串行执行。 */
  private withLock<T>(id: string, task: () => Promise<T>): Promise<T> {
    const previous = this.locks.get(id) ?? Promise.resolve();
    const run = previous.then(task, task);
    this.locks.set(
      id,
      run.then(
        () => undefined,
        () => undefined,
      ),
    );
    return run;
  }

  async list(): Promise<SessionSummary[]> {
    let fileNames: string[];
    try {
      fileNames = await readdir(this.directory);
    } catch {
      return []; // 目录还不存在 = 还没有会话
    }
    const sessions = await Promise.all(
      fileNames
        .filter((name) => name.endsWith('.json'))
        .map(async (name) => {
          try {
            const raw = await readFile(path.join(this.directory, name), 'utf8');
            return normalizeSession(JSON.parse(raw));
          } catch {
            return null; // 单个文件损坏不应让整个列表不可用
          }
        }),
    );
    return sessions
      .filter((session): session is LocalSession => session !== null)
      .sort((a, b) => {
        if (a.updatedAt !== b.updatedAt) return a.updatedAt < b.updatedAt ? 1 : -1;
        return a.id < b.id ? -1 : 1;
      })
      .map(toSummary);
  }

  async get(id: string): Promise<LocalSession | null> {
    const raw = await readFile(this.filePath(id), 'utf8').catch((error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    if (raw === null) return null;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new Error('会话数据已损坏,无法读取。');
    }
    const session = normalizeSession(parsed);
    if (!session) throw new Error('会话数据已损坏,无法读取。');
    return session;
  }

  async create(input: CreateSessionInput = {}): Promise<LocalSession> {
    const title = typeof input.title === 'string' && input.title.trim() ? input.title.trim() : DEFAULT_SESSION_TITLE;
    const now = new Date().toISOString();
    const session: LocalSession = {
      id: randomUUID(),
      title,
      workspaceRoot: input.workspaceRoot ?? null,
      createdAt: now,
      updatedAt: now,
      messageCount: 0,
      messages: [],
    };
    await this.save(session);
    return session;
  }

  async rename(id: string, title: string): Promise<LocalSession> {
    const nextTitle = typeof title === 'string' ? title.trim() : '';
    if (!nextTitle) throw new Error('会话标题不能为空。');
    return this.withLock(id, async () => {
      const session = await this.require(id);
      const next: LocalSession = {
        ...session,
        title: nextTitle,
        updatedAt: nextTimestamp(session.updatedAt),
      };
      await this.save(next);
      return next;
    });
  }

  async remove(id: string): Promise<void> {
    await this.withLock(id, async () => {
      await rm(this.filePath(id), { force: true });
    });
  }

  async appendMessage(id: string, input: AppendMessageInput): Promise<AppendMessageResult> {
    return this.withLock(id, async () => {
      const session = await this.require(id);
      const updatedAt = nextTimestamp(session.updatedAt);
      const message = createChatMessage(input, updatedAt);
      const next: LocalSession = {
        ...session,
        messages: [...session.messages, message],
        messageCount: session.messages.length + 1,
        updatedAt,
      };
      await this.save(next);
      return { session: next, message };
    });
  }

  async updateMessage(id: string, messageId: string, patch: ChatMessagePatch): Promise<LocalSession> {
    return this.withLock(id, async () => {
      const session = await this.require(id);
      const index = session.messages.findIndex((message) => message.id === messageId);
      if (index < 0) throw new Error('要更新的消息不存在。');
      const current = session.messages[index];
      const updated: ChatMessage = { ...current };
      if (typeof patch.content === 'string') updated.content = patch.content;
      if (patch.toolStatus) updated.toolStatus = patch.toolStatus;
      if (patch.toolName) updated.toolName = patch.toolName;
      const messages = [...session.messages];
      messages[index] = updated;
      const next: LocalSession = {
        ...session,
        messages,
        updatedAt: nextTimestamp(session.updatedAt),
      };
      await this.save(next);
      return next;
    });
  }

  async removeMessage(id: string, messageId: string): Promise<LocalSession> {
    return this.withLock(id, async () => {
      const session = await this.require(id);
      const messages = session.messages.filter((message) => message.id !== messageId);
      if (messages.length === session.messages.length) return session;
      const next: LocalSession = {
        ...session,
        messages,
        messageCount: messages.length,
        updatedAt: nextTimestamp(session.updatedAt),
      };
      await this.save(next);
      return next;
    });
  }

  private async require(id: string): Promise<LocalSession> {
    const session = await this.get(id);
    if (!session) throw new Error('会话不存在,可能已被删除。');
    return session;
  }

  /** 原子写入:先写临时文件再 rename,避免读到半截 JSON。 */
  private async save(session: LocalSession): Promise<void> {
    await mkdir(this.directory, { recursive: true });
    const filePath = this.filePath(session.id);
    const tmpPath = `${filePath}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
    const payload: LocalSession = { ...session, messageCount: session.messages.length };
    try {
      await writeFile(tmpPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
      await rename(tmpPath, filePath);
    } catch (error) {
      await rm(tmpPath, { force: true }).catch(() => undefined);
      throw error;
    }
  }
}
