/**
 * 主进程 IPC 注册层。
 *
 * 这是 electron 依赖的边界之一:所有 ipcMain.handle 都按 src/shared/ipc.ts 的常量注册,
 * 并把 AgentEvent 通过 webContents.send 推给所有窗口。
 * 具体业务逻辑在 service.ts / local/*,这里只做参数校验、平台能力(dialog/shell)与转发。
 */
import { BrowserWindow, app, dialog, ipcMain, shell } from 'electron';
import type { OpenDialogOptions } from 'electron';

import type { AgentEvent, DesktopSettings, SendMessageInput } from '@shared/contract';
import { IPC } from '@shared/ipc';

import { DesktopService } from './service';

/** 把 AgentEvent 广播给所有存活窗口。 */
export function broadcastAgentEvent(event: AgentEvent): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (window.isDestroyed()) continue;
    window.webContents.send(IPC.agentEvent, event);
  }
}

/** 创建服务对象(必须在 app ready 之后调用,getPath('userData') 依赖 ready)。 */
export function createDesktopService(): DesktopService {
  return new DesktopService({
    userDataDir: app.getPath('userData'),
    onEvent: broadcastAgentEvent,
  });
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== 'string') {
    throw new Error(`参数「${name}」必须是字符串。`);
  }
  return value;
}

function parseSendMessageInput(value: unknown): SendMessageInput {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('发送消息的参数不合法。');
  }
  const record = value as { sessionId?: unknown; text?: unknown };
  return {
    sessionId: requireString(record.sessionId, 'sessionId'),
    text: requireString(record.text, 'text'),
  };
}

/**
 * 校验「要在系统浏览器打开的控制台地址」。
 * 只放行 http/https,挡住 file:/javascript: 等可能被滥用的协议。
 */
function requireHttpUrl(rawUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error('云端控制台地址不合法,请检查设置。');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('只允许在系统浏览器中打开 http/https 地址。');
  }
  return parsed.toString();
}

/** 目录选择框参数:允许创建目录,只在本地电脑模式使用。 */
const CHOOSE_DIRECTORY_OPTIONS: OpenDialogOptions = {
  title: '选择本地工作区目录',
  buttonLabel: '选择',
  properties: ['openDirectory', 'createDirectory'],
};

/** 按 src/shared/ipc.ts 的常量注册全部 handler(重复调用前应先清理)。 */
export function registerIpcHandlers(service: DesktopService): void {
  /* ------------------------------ 设置 ------------------------------ */
  ipcMain.handle(IPC.settingsRead, () => service.readSettings());

  ipcMain.handle(IPC.settingsUpdate, (_event, patch: unknown) => {
    if (!patch || typeof patch !== 'object' || Array.isArray(patch)) {
      throw new Error('设置参数不合法。');
    }
    return service.updateSettings(patch as Partial<DesktopSettings>);
  });

  /* ----------------------------- 工作区 ----------------------------- */
  ipcMain.handle(IPC.workspaceInfo, () => service.workspaceInfo());

  ipcMain.handle(IPC.workspaceChoose, async () => {
    const parent = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null;
    const result = parent
      ? await dialog.showOpenDialog(parent, CHOOSE_DIRECTORY_OPTIONS)
      : await dialog.showOpenDialog(CHOOSE_DIRECTORY_OPTIONS);
    const selected = result.canceled ? undefined : result.filePaths[0];
    if (!selected) return null;
    return service.chooseWorkspace(async () => selected);
  });

  ipcMain.handle(IPC.workspaceList, (_event, relPath: unknown) =>
    service.listWorkspace(typeof relPath === 'string' ? relPath : ''),
  );

  ipcMain.handle(IPC.workspaceRead, (_event, relPath: unknown) =>
    service.readWorkspaceFile(requireString(relPath, 'path')),
  );

  /* ------------------------------ 会话 ------------------------------ */
  ipcMain.handle(IPC.sessionsList, () => service.listSessions());

  ipcMain.handle(IPC.sessionsGet, (_event, id: unknown) =>
    service.getSession(requireString(id, 'id')),
  );

  ipcMain.handle(IPC.sessionsCreate, (_event, title: unknown) =>
    service.createSession(typeof title === 'string' ? title : undefined),
  );

  ipcMain.handle(IPC.sessionsRename, (_event, id: unknown, title: unknown) =>
    service.renameSession(requireString(id, 'id'), requireString(title, 'title')),
  );

  ipcMain.handle(IPC.sessionsRemove, (_event, id: unknown) =>
    service.removeSession(requireString(id, 'id')),
  );

  /* ------------------------------ Agent ----------------------------- */
  ipcMain.handle(IPC.agentSend, (_event, input: unknown) =>
    service.sendMessage(parseSendMessageInput(input)),
  );

  ipcMain.handle(IPC.agentCancel, (_event, sessionId: unknown) =>
    service.cancelAgent(requireString(sessionId, 'sessionId')),
  );

  /* ------------------------------ 命令 ------------------------------ */
  ipcMain.handle(IPC.shellRun, (_event, command: unknown) =>
    service.runShellCommand(requireString(command, 'command')),
  );

  /* ------------------------------ 云端 ------------------------------ */
  ipcMain.handle(IPC.cloudOpenConsole, async (_event, rawUrl: unknown) => {
    // 未显式传 URL 时用设置里的云端控制台地址。
    const target =
      typeof rawUrl === 'string' && rawUrl.trim()
        ? rawUrl.trim()
        : (await service.readSettings()).cloudConsoleUrl;
    if (!target.trim()) {
      throw new Error('尚未配置云端控制台地址,请先到设置里填写。');
    }
    await shell.openExternal(requireHttpUrl(target.trim()));
  });
}

/** 退出前移除 handler(应用退出/热重启场景避免重复注册)。 */
export function unregisterIpcHandlers(): void {
  for (const channel of Object.values(IPC)) {
    ipcMain.removeHandler(channel);
  }
}
