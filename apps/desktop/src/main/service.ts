/**
 * 桌面主进程的服务组装层。
 *
 * 把设置、会话、工作区、命令执行器与本地 Agent 组装成 IPC 层直接可用的对象。
 * 这里刻意不 import electron:窗口、对话框、shell.openExternal 等平台能力
 * 留在 main.ts / ipc.ts,本模块只依赖 src/main/local/* 与共享契约。
 */
import path from 'node:path';

import type {
  AgentEvent,
  CommandResult,
  DesktopSettings,
  FileContent,
  FileEntry,
  LocalSession,
  SendMessageInput,
  SessionSummary,
  WorkspaceInfo,
} from '@shared/contract';

import { LocalAgent } from './local/agent';
import { runCommand } from './local/command-runner';
import { SessionStore } from './local/session-store';
import { SettingsStore } from './local/settings-store';
import { describeWorkspace, readTextFile, resolveWithin, workspaceApi } from './local/workspace';

export interface DesktopServiceOptions {
  /** Electron 的 app.getPath('userData')。 */
  userDataDir: string;
  /** Agent 事件出口:ipc.ts 会接到 BrowserWindow 的 webContents.send 上。 */
  onEvent: (event: AgentEvent) => void;
  /** 测试注入用;默认 globalThis.fetch。 */
  fetch?: typeof fetch;
}

/** 选择目录的回调:main.ts/ipc.ts 用 Electron dialog 实现,这里只依赖抽象。 */
export type DirectoryPicker = () => Promise<string | null>;

export class DesktopService {
  readonly settings: SettingsStore;
  readonly sessions: SessionStore;
  readonly agent: LocalAgent;

  private readonly onEvent: (event: AgentEvent) => void;

  constructor(options: DesktopServiceOptions) {
    this.onEvent = options.onEvent;
    this.settings = new SettingsStore({ userDataDir: options.userDataDir });
    this.sessions = new SessionStore(path.join(options.userDataDir, 'sessions'));
    this.agent = new LocalAgent({
      sessions: this.sessions,
      workspace: workspaceApi,
      runCommand: (command, runOptions) =>
        runCommand(command, {
          cwd: runOptions.cwd,
          signal: runOptions.signal,
        }).promise,
      readSettings: () => this.settings.read(),
      onEvent: options.onEvent,
      fetch: options.fetch,
    });
  }

  /* ------------------------------ 设置 ------------------------------ */

  readSettings(): Promise<DesktopSettings> {
    return this.settings.read();
  }

  updateSettings(patch: Partial<DesktopSettings>): Promise<DesktopSettings> {
    return this.settings.update(patch);
  }

  /* ----------------------------- 工作区 ----------------------------- */

  /** 当前工作区(从设置里的 workspaceRoot 推导)。 */
  async workspaceInfo(): Promise<WorkspaceInfo | null> {
    const settings = await this.settings.read();
    if (!settings.workspaceRoot) return null;
    return describeWorkspace(settings.workspaceRoot);
  }

  /** 弹目录选择框(由调用方注入),选中后写入设置并返回工作区信息。 */
  async chooseWorkspace(pick: DirectoryPicker): Promise<WorkspaceInfo | null> {
    const chosen = await pick();
    if (!chosen) return null;
    // 选中目录必须存在且可解析,避免把无效路径写进设置后每个操作都报错。
    await resolveWithin(chosen);
    const updated = await this.settings.update({ workspaceRoot: chosen });
    return updated.workspaceRoot ? describeWorkspace(updated.workspaceRoot) : null;
  }

  async listWorkspace(relPath = ''): Promise<FileEntry[]> {
    const root = await this.requireWorkspaceRoot();
    return workspaceApi.listDirectory(root, relPath);
  }

  async readWorkspaceFile(relPath: string): Promise<FileContent> {
    const root = await this.requireWorkspaceRoot();
    return readTextFile(root, relPath);
  }

  private async requireWorkspaceRoot(): Promise<string> {
    const settings = await this.settings.read();
    const root = settings.workspaceRoot?.trim();
    if (!root) throw new Error('尚未选择工作区目录,请先选择一个本地目录。');
    return root;
  }

  /* ------------------------------ 会话 ------------------------------ */

  listSessions(): Promise<SessionSummary[]> {
    return this.sessions.list();
  }

  getSession(id: string): Promise<LocalSession | null> {
    return this.sessions.get(id);
  }

  async createSession(title?: string): Promise<LocalSession> {
    const settings = await this.settings.read();
    return this.sessions.create({ title, workspaceRoot: settings.workspaceRoot });
  }

  renameSession(id: string, title: string): Promise<LocalSession> {
    return this.sessions.rename(id, title);
  }

  removeSession(id: string): Promise<void> {
    return this.sessions.remove(id);
  }

  /* ------------------------------ Agent ----------------------------- */

  async sendMessage(input: SendMessageInput): Promise<void> {
    await this.agent.send(input);
  }

  cancelAgent(sessionId: string): Promise<void> {
    return this.agent.cancel(sessionId);
  }

  /* ------------------------------ 命令 ------------------------------ */

  /** 在工作区根目录手动执行一条命令(界面「终端」入口)。 */
  async runShellCommand(command: string): Promise<CommandResult> {
    const cwd = await this.requireWorkspaceRoot();
    return runCommand(command, { cwd }).promise;
  }

  /** 退出流程:中止所有会话执行。 */
  async shutdown(): Promise<void> {
    await this.agent.shutdown();
  }

  /** 供 ipc 层获取事件出口(调试/测试用)。 */
  emitEvent(event: AgentEvent): void {
    this.onEvent(event);
  }
}
