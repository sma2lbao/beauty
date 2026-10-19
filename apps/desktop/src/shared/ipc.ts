/**
 * IPC 通道名。主进程注册 handler、预加载脚本 invoke/on 都从这里取,
 * 避免字符串在两侧各写一份导致拼错后静默失效。
 */
export const IPC = {
  settingsRead: 'beauty:settings:read',
  settingsUpdate: 'beauty:settings:update',

  workspaceInfo: 'beauty:workspace:info',
  workspaceChoose: 'beauty:workspace:choose',
  workspaceList: 'beauty:workspace:list',
  workspaceRead: 'beauty:workspace:read',

  sessionsList: 'beauty:sessions:list',
  sessionsGet: 'beauty:sessions:get',
  sessionsCreate: 'beauty:sessions:create',
  sessionsRename: 'beauty:sessions:rename',
  sessionsRemove: 'beauty:sessions:remove',

  agentSend: 'beauty:agent:send',
  agentCancel: 'beauty:agent:cancel',
  /** 主进程 → 渲染进程的事件推送通道。 */
  agentEvent: 'beauty:agent:event',

  shellRun: 'beauty:shell:run',

  cloudOpenConsole: 'beauty:cloud:open-console',
} as const;

export type IpcChannel = (typeof IPC)[keyof typeof IPC];
