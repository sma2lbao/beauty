/**
 * 主进程 ↔ 渲染进程的共享契约。
 *
 * 这里是桌面应用唯一的集成点:主进程实现、预加载桥接、渲染进程 UI 三方
 * 都只依赖本文件的类型,任何一方改协议必须先改这里。IPC 通道名见 ipc.ts。
 */

/** 运行模式:本地电脑(会话/文件/命令都在本机执行)或云端(原有服务端编排)。 */
export type LocalMode = 'local' | 'cloud';

/** 模型端点设置:任何 OpenAI 兼容端点都可用(云端 API 或本地 Ollama / vLLM)。 */
export interface ModelSettings {
  /** 形如 https://api.deepseek.com/v1、http://127.0.0.1:11434/v1(不带结尾斜杠亦可)。 */
  baseUrl: string;
  /** 模型名,如 deepseek-chat、qwen2.5-coder。 */
  model: string;
  /** 端点密钥;本地模型可留空。 */
  apiKey: string;
}

/** 桌面应用设置,持久化在 userData/settings.json。 */
export interface DesktopSettings {
  mode: LocalMode;
  /** 云端模式要打开的控制台地址(apps/web 构建产物或已部署站点)。 */
  cloudConsoleUrl: string;
  model: ModelSettings;
  /** 当前本地工作区根目录;null 表示用户还没选过目录。 */
  workspaceRoot: string | null;
}

/** 已打开的本地工作区。 */
export interface WorkspaceInfo {
  root: string;
  /** 目录名,用于界面标题。 */
  name: string;
  openedAt: string;
}

/** 工作区目录项。 */
export interface FileEntry {
  /** 相对工作区根的 POSIX 路径,根目录下的直接子项形如 "src"。 */
  path: string;
  name: string;
  kind: 'file' | 'directory';
  size: number;
  modifiedAt: string;
}

/** 工作区文件内容。 */
export interface FileContent {
  path: string;
  content: string;
  size: number;
  /** 超过读取上限被截断(避免把二进制/大文件拖进渲染进程)。 */
  truncated: boolean;
}

export type ChatRole = 'user' | 'assistant' | 'tool';

/** 会话里的一条消息。tool 消息记录一次本地工具调用。 */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  /** role=tool 时:工具名与执行状态。 */
  toolName?: string;
  toolStatus?: 'running' | 'ok' | 'error';
}

/** 会话列表项(不含消息体)。 */
export interface SessionSummary {
  id: string;
  title: string;
  /** 会话创建时绑定的工作区;换目录后旧会话仍指向原目录。 */
  workspaceRoot: string | null;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
}

/** 会话全量快照。 */
export interface LocalSession extends SessionSummary {
  messages: ChatMessage[];
}

/** 主进程推给渲染进程的实时事件。 */
export type AgentEvent =
  /** 会话状态整体变化(消息追加、工具状态翻转等)。 */
  | { type: 'session.updated'; session: LocalSession }
  /** 助手正文增量(边产出边渲染)。 */
  | {
      type: 'assistant.delta';
      sessionId: string;
      messageId: string;
      delta: string;
    }
  /** 一次本地运行开始/结束。 */
  | { type: 'run.started'; sessionId: string }
  | {
      type: 'run.finished';
      sessionId: string;
      status: 'ok' | 'error' | 'cancelled';
      error?: string;
    };

export interface SendMessageInput {
  sessionId: string;
  text: string;
}

/** 手动执行一条本机命令的结果(界面里的"终端"入口)。 */
export interface CommandResult {
  command: string;
  exitCode: number;
  stdout: string;
  stderr: string;
  durationMs: number;
  /** 输出超过上限被截断。 */
  truncated: boolean;
  cancelled: boolean;
}

/**
 * 预加载脚本注入到渲染进程的桥接对象(window.beautyDesktop)。
 * 所有方法失败时抛 Error,message 面向用户可读。
 */
export interface DesktopBridge {
  platform: 'darwin' | 'win32' | 'linux';
  appVersion: string;
  settings: {
    read(): Promise<DesktopSettings>;
    update(patch: Partial<DesktopSettings>): Promise<DesktopSettings>;
  };
  workspace: {
    /** 当前工作区(未选择时为 null)。 */
    info(): Promise<WorkspaceInfo | null>;
    /** 弹系统目录选择框;取消返回 null。 */
    choose(): Promise<WorkspaceInfo | null>;
    /** 列出目录(相对工作区根的路径,空串表示根)。 */
    list(path: string): Promise<FileEntry[]>;
    read(path: string): Promise<FileContent>;
  };
  sessions: {
    list(): Promise<SessionSummary[]>;
    get(id: string): Promise<LocalSession | null>;
    create(title?: string): Promise<LocalSession>;
    rename(id: string, title: string): Promise<LocalSession>;
    remove(id: string): Promise<void>;
  };
  agent: {
    /** 追加一条用户消息并在本机开始一轮执行(立即返回,进度走事件)。 */
    send(input: SendMessageInput): Promise<void>;
    /** 中止正在跑的一轮。 */
    cancel(sessionId: string): Promise<void>;
    /** 订阅实时事件,返回退订函数。 */
    subscribe(listener: (event: AgentEvent) => void): () => void;
  };
  shell: {
    /** 在工作区根目录手动执行一条命令(危险操作由 UI 二次确认)。 */
    run(command: string): Promise<CommandResult>;
  };
  cloud: {
    /** 在系统浏览器打开云端控制台。 */
    openConsole(url?: string): Promise<void>;
  };
}
