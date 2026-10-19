/**
 * 预加载脚本:把主进程能力以最小面暴露给渲染进程。
 *
 * 安全约束(sandbox: true 下同样成立):
 * - 只经 contextBridge 暴露 DesktopBridge 契约里列出的方法,不暴露 ipcRenderer 本身;
 * - 所有调用走 ipcRenderer.invoke(主进程按 ipc.ts 的常量校验参数);
 * - 输出为 CommonJS(dist/preload/index.cjs),不要写顶层 await。
 */
import { contextBridge, ipcRenderer } from 'electron';

import type { AgentEvent, DesktopBridge } from '@shared/contract';
import { IPC } from '@shared/ipc';

/** main.ts 通过 additionalArguments 注入应用版本(沙箱里读不到 app 对象)。 */
function readAppVersion(): string {
  const prefix = '--beauty-app-version=';
  const arg = process.argv.find((item) => item.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : '0.0.0';
}

const bridge: DesktopBridge = {
  platform: process.platform as DesktopBridge['platform'],
  appVersion: readAppVersion(),

  settings: {
    read: () => ipcRenderer.invoke(IPC.settingsRead),
    update: (patch) => ipcRenderer.invoke(IPC.settingsUpdate, patch),
  },

  workspace: {
    info: () => ipcRenderer.invoke(IPC.workspaceInfo),
    choose: () => ipcRenderer.invoke(IPC.workspaceChoose),
    list: (relPath) => ipcRenderer.invoke(IPC.workspaceList, relPath),
    read: (relPath) => ipcRenderer.invoke(IPC.workspaceRead, relPath),
  },

  sessions: {
    list: () => ipcRenderer.invoke(IPC.sessionsList),
    get: (id) => ipcRenderer.invoke(IPC.sessionsGet, id),
    create: (title) => ipcRenderer.invoke(IPC.sessionsCreate, title),
    rename: (id, title) => ipcRenderer.invoke(IPC.sessionsRename, id, title),
    remove: (id) => ipcRenderer.invoke(IPC.sessionsRemove, id),
  },

  agent: {
    send: (input) => ipcRenderer.invoke(IPC.agentSend, input),
    cancel: (sessionId) => ipcRenderer.invoke(IPC.agentCancel, sessionId),
    subscribe: (listener) => {
      const handler = (_event: Electron.IpcRendererEvent, payload: AgentEvent): void => {
        listener(payload);
      };
      ipcRenderer.on(IPC.agentEvent, handler);
      // 返回退订函数:React effect 清理时会用到,避免重复订阅。
      return () => {
        ipcRenderer.removeListener(IPC.agentEvent, handler);
      };
    },
  },

  shell: {
    run: (command) => ipcRenderer.invoke(IPC.shellRun, command),
  },

  cloud: {
    openConsole: (url) => ipcRenderer.invoke(IPC.cloudOpenConsole, url),
  },
};

contextBridge.exposeInMainWorld('beautyDesktop', bridge);
