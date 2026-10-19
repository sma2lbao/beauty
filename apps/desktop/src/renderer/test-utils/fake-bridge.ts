/**
 * 测试用假桥接:在 jsdom 里模拟 preload 注入的 window.beautyDesktop。
 *
 * 刻意不 import electron / 主进程代码:渲染进程测试只依赖 @shared/contract 的
 * 类型。事件推送通过 emit 手动触发,便于精确断言 UI 对 session.updated /
 * assistant.delta / run.started / run.finished 的反应。
 */
import { vi } from 'vitest';

import type {
  AgentEvent,
  CommandResult,
  DesktopBridge,
  DesktopSettings,
  FileContent,
  FileEntry,
  LocalSession,
  SendMessageInput,
  SessionSummary,
  WorkspaceInfo,
} from '@shared/contract';

export const DEFAULT_WORKSPACE: WorkspaceInfo = {
  root: '/tmp/beauty-demo',
  name: 'beauty-demo',
  openedAt: '2026-10-18T02:00:00.000Z',
};

export const DEFAULT_SETTINGS: DesktopSettings = {
  mode: 'local',
  cloudConsoleUrl: 'https://console.example.com',
  model: {
    baseUrl: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    apiKey: '',
  },
  workspaceRoot: DEFAULT_WORKSPACE.root,
};

/** 构造一条会话消息/快照,未传的字段用稳定的默认值。 */
export function makeSession(
  overrides: Partial<LocalSession> = {},
): LocalSession {
  return {
    id: 's1',
    title: '整理本周邮件',
    workspaceRoot: DEFAULT_WORKSPACE.root,
    createdAt: '2026-10-18T02:00:00.000Z',
    updatedAt: '2026-10-18T02:05:00.000Z',
    messageCount: 0,
    messages: [],
    ...overrides,
  };
}

export function toSummary(session: LocalSession): SessionSummary {
  return {
    id: session.id,
    title: session.title,
    workspaceRoot: session.workspaceRoot,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messageCount,
  };
}

export function makeFile(
  path: string,
  kind: FileEntry['kind'] = 'file',
  size = 1024,
): FileEntry {
  const name = path.includes('/')
    ? path.slice(path.lastIndexOf('/') + 1)
    : path;
  return {
    path,
    name,
    kind,
    size,
    modifiedAt: '2026-10-18T02:00:00.000Z',
  };
}

export interface FakeBridgeOptions {
  settings?: Partial<DesktopSettings>;
  /** 传 null 表示用户还没选过目录。 */
  workspace?: WorkspaceInfo | null;
  sessions?: SessionSummary[];
  session?: LocalSession | null;
  directories?: Record<string, FileEntry[]>;
  contents?: Record<string, FileContent>;
}

/** 安装假桥接并返回句柄;afterEach 里调 removeFakeBridge 清理。 */
export function installFakeBridge(options: FakeBridgeOptions = {}) {
  const fake = createFakeBridge(options);
  window.beautyDesktop = fake.bridge;
  return fake;
}

/** 模拟「用浏览器直接打开渲染进程」:桥接不存在。 */
export function removeFakeBridge(): void {
  Reflect.deleteProperty(window, 'beautyDesktop');
}

export function createFakeBridge(options: FakeBridgeOptions = {}) {
  const listeners = new Set<(event: AgentEvent) => void>();

  let settings: DesktopSettings = {
    ...DEFAULT_SETTINGS,
    ...options.settings,
    model: { ...DEFAULT_SETTINGS.model, ...options.settings?.model },
  };
  let workspace: WorkspaceInfo | null =
    options.workspace === undefined ? DEFAULT_WORKSPACE : options.workspace;
  let sessions: SessionSummary[] = options.sessions ?? [];
  const sessionStore = new Map<string, LocalSession>();

  if (options.session) sessionStore.set(options.session.id, options.session);
  for (const summary of sessions) {
    if (!sessionStore.has(summary.id)) {
      sessionStore.set(summary.id, makeSession({ ...summary }));
    }
  }

  const directories = options.directories ?? {};
  const contents = options.contents ?? {};
  let created = 0;

  const settingsRead = vi.fn(async () => settings);
  const settingsUpdate = vi.fn(async (patch: Partial<DesktopSettings>) => {
    settings = {
      ...settings,
      ...patch,
      model: patch.model ? { ...patch.model } : settings.model,
    };
    return settings;
  });

  const workspaceInfo = vi.fn(async () => workspace);
  const workspaceChoose = vi.fn(async () => {
    workspace = DEFAULT_WORKSPACE;
    settings = { ...settings, workspaceRoot: DEFAULT_WORKSPACE.root };
    return workspace;
  });
  const workspaceList = vi.fn(
    async (path: string): Promise<FileEntry[]> => directories[path] ?? [],
  );
  const workspaceRead = vi.fn(
    async (path: string): Promise<FileContent> =>
      contents[path] ?? { path, content: '', size: 0, truncated: false },
  );

  const sessionsList = vi.fn(async () => sessions);
  const sessionsGet = vi.fn(async (id: string) => sessionStore.get(id) ?? null);
  const sessionsCreate = vi.fn(async (title?: string) => {
    created += 1;
    const session = makeSession({
      id: `s${created + sessionStore.size}`,
      title: title ?? `新会话 ${created}`,
      createdAt: '2026-10-18T03:00:00.000Z',
      updatedAt: '2026-10-18T03:00:00.000Z',
    });
    sessionStore.set(session.id, session);
    sessions = [toSummary(session), ...sessions];
    return session;
  });
  const sessionsRename = vi.fn(async (id: string, title: string) => {
    const current = sessionStore.get(id);
    if (!current) throw new Error('会话不存在');
    const next = { ...current, title, updatedAt: '2026-10-18T04:00:00.000Z' };
    sessionStore.set(id, next);
    sessions = sessions.map((item) =>
      item.id === id ? toSummary(next) : item,
    );
    return next;
  });
  const sessionsRemove = vi.fn(async (id: string) => {
    sessionStore.delete(id);
    sessions = sessions.filter((item) => item.id !== id);
  });

  const agentSend = vi.fn(async (_input: SendMessageInput) => undefined);
  const agentCancel = vi.fn(async (_sessionId: string) => undefined);
  const agentSubscribe = vi.fn(
    (listener: (event: AgentEvent) => void): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  );

  const shellRun = vi.fn(
    async (command: string): Promise<CommandResult> => ({
      command,
      exitCode: 0,
      stdout: '',
      stderr: '',
      durationMs: 1,
      truncated: false,
      cancelled: false,
    }),
  );
  const cloudOpenConsole = vi.fn(async (_url?: string) => undefined);

  const bridge: DesktopBridge = {
    platform: 'darwin',
    appVersion: '0.0.0-test',
    settings: { read: settingsRead, update: settingsUpdate },
    workspace: {
      info: workspaceInfo,
      choose: workspaceChoose,
      list: workspaceList,
      read: workspaceRead,
    },
    sessions: {
      list: sessionsList,
      get: sessionsGet,
      create: sessionsCreate,
      rename: sessionsRename,
      remove: sessionsRemove,
    },
    agent: { send: agentSend, cancel: agentCancel, subscribe: agentSubscribe },
    shell: { run: shellRun },
    cloud: { openConsole: cloudOpenConsole },
  };

  return {
    bridge,
    mocks: {
      settingsRead,
      settingsUpdate,
      workspaceInfo,
      workspaceChoose,
      workspaceList,
      workspaceRead,
      sessionsList,
      sessionsGet,
      sessionsCreate,
      sessionsRename,
      sessionsRemove,
      agentSend,
      agentCancel,
      agentSubscribe,
      shellRun,
      cloudOpenConsole,
    },
    /** 模拟主进程推送事件。 */
    emit(event: AgentEvent): void {
      for (const listener of listeners) listener(event);
    },
  };
}
