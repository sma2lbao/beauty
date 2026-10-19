/**
 * 本地 Agent 循环。
 *
 * 调用任意 OpenAI 兼容端点(`POST {baseUrl}/chat/completions`,stream: true),
 * 在「模型 → 工具 → 模型」之间循环,把过程实时推给渲染进程:
 * - 助手正文增量 → assistant.delta 事件(边产出边渲染);
 * - 每轮工具调用 → session.updated 事件(工具消息状态 running → ok/error);
 * - 一轮执行开始/结束 → run.started / run.finished。
 *
 * 工具只有四个:list_files / read_file / write_file / run_command,
 * 全部限定在工作区内执行,命令可被会话取消信号中止。
 * 本模块不 import electron;fetch、存储、命令执行器都由调用方注入,便于纯 node 测试。
 */
import type {
  AgentEvent,
  ChatMessage,
  CommandResult,
  DesktopSettings,
  FileEntry,
  LocalSession,
  SendMessageInput,
} from '@shared/contract';

import type { WorkspaceApi } from './workspace';

/** 最多执行多少轮「模型 + 工具」,防止模型死循环。 */
export const DEFAULT_MAX_ITERATIONS = 12;

/** 单次工具结果回填给模型的最大字符数,避免把巨大文件塞进上下文。 */
export const DEFAULT_MAX_TOOL_OUTPUT_CHARS = 8000;

/** Agent 依赖的会话存储子集(SessionStore 结构上满足)。 */
export interface AgentSessionStore {
  get(id: string): Promise<LocalSession | null>;
  appendMessage(
    id: string,
    input: {
      role: ChatMessage['role'];
      content: string;
      toolName?: string;
      toolStatus?: ChatMessage['toolStatus'];
    },
  ): Promise<{ session: LocalSession; message: ChatMessage }>;
  updateMessage(
    id: string,
    messageId: string,
    patch: { content?: string; toolName?: string; toolStatus?: ChatMessage['toolStatus'] },
  ): Promise<LocalSession>;
  removeMessage(id: string, messageId: string): Promise<LocalSession>;
}

export interface LocalAgentOptions {
  sessions: AgentSessionStore;
  workspace: WorkspaceApi;
  /** 在工作区里执行命令;由调用方注入(便于测试替身)。 */
  runCommand: (command: string, options: { cwd: string; signal: AbortSignal }) => Promise<CommandResult>;
  readSettings: () => Promise<DesktopSettings>;
  onEvent: (event: AgentEvent) => void;
  /** 默认 globalThis.fetch;测试注入 fake。 */
  fetch?: typeof fetch;
  maxIterations?: number;
  maxToolOutputChars?: number;
}

/** 会话被取消时使用的内部错误,靠它区分「取消」与「失败」。 */
export class AgentCancelledError extends Error {
  constructor() {
    super('本轮执行已取消。');
    this.name = 'AgentCancelledError';
  }
}

/* ------------------------------------------------------------------ */
/* OpenAI 兼容协议相关的内部类型                                         */
/* ------------------------------------------------------------------ */

interface OpenAiToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}

type OpenAiMessage =
  | { role: 'system' | 'user' | 'assistant'; content: string; tool_calls?: OpenAiToolCall[] }
  | { role: 'tool'; content: string; tool_call_id: string };

interface ToolCallDraft {
  id: string;
  name: string;
  arguments: string;
}

interface CompletionOutcome {
  content: string;
  toolCalls: ToolCallDraft[];
}

interface StreamState {
  content: string;
  toolCalls: ToolCallDraft[];
}

interface ToolOutcome {
  ok: boolean;
  content: string;
}

function createStreamState(): StreamState {
  return { content: '', toolCalls: [] };
}

/** 累积一个流式 tool_calls 分片(按 index 聚合 id / name / arguments)。 */
function applyToolCallDelta(raw: unknown, state: StreamState): void {
  if (!raw || typeof raw !== 'object') return;
  const record = raw as {
    index?: unknown;
    id?: unknown;
    function?: { name?: unknown; arguments?: unknown } | null;
  };
  const index = typeof record.index === 'number' && record.index >= 0 ? record.index : state.toolCalls.length;
  let draft = state.toolCalls[index];
  if (!draft) {
    draft = { id: '', name: '', arguments: '' };
    state.toolCalls[index] = draft;
  }
  if (typeof record.id === 'string' && record.id) draft.id = record.id;
  const fn = record.function;
  if (fn && typeof fn === 'object') {
    // 名称通常一次给全;arguments 会分多个分片,必须拼接。
    if (typeof fn.name === 'string' && fn.name) draft.name = fn.name;
    if (typeof fn.arguments === 'string') draft.arguments += fn.arguments;
  }
}

/** 解析一个 SSE data 块(可能包含多个 choice)并累积到 state。 */
function applyStreamPayload(payload: unknown, state: StreamState, onDelta: (delta: string) => void): void {
  if (!payload || typeof payload !== 'object') return;
  const choices = (payload as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return;
  for (const choice of choices) {
    if (!choice || typeof choice !== 'object') continue;
    const delta = (choice as { delta?: unknown }).delta;
    if (!delta || typeof delta !== 'object') continue;
    const content = (delta as { content?: unknown }).content;
    if (typeof content === 'string' && content) {
      state.content += content;
      onDelta(content);
    }
    const toolCalls = (delta as { tool_calls?: unknown }).tool_calls;
    if (Array.isArray(toolCalls)) {
      for (const toolCall of toolCalls) applyToolCallDelta(toolCall, state);
    }
  }
}

/** 解析非流式 JSON 响应(部分端点/代理会忽略 stream:true)。 */
function parseCompletionJson(payload: unknown, onDelta: (delta: string) => void): CompletionOutcome {
  const state = createStreamState();
  if (!payload || typeof payload !== 'object') return { content: '', toolCalls: [] };
  const choices = (payload as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) return { content: '', toolCalls: [] };
  const message = (choices[0] as { message?: unknown }).message;
  if (!message || typeof message !== 'object') return { content: '', toolCalls: [] };
  const content = (message as { content?: unknown }).content;
  if (typeof content === 'string' && content) {
    state.content = content;
    onDelta(content);
  }
  const toolCalls = (message as { tool_calls?: unknown }).tool_calls;
  if (Array.isArray(toolCalls)) {
    for (const toolCall of toolCalls) {
      if (!toolCall || typeof toolCall !== 'object') continue;
      const record = toolCall as {
        id?: unknown;
        function?: { name?: unknown; arguments?: unknown } | null;
      };
      state.toolCalls.push({
        id: typeof record.id === 'string' ? record.id : '',
        name: typeof record.function?.name === 'string' ? record.function.name : '',
        arguments: typeof record.function?.arguments === 'string' ? record.function.arguments : '',
      });
    }
  }
  return { content: state.content, toolCalls: state.toolCalls.filter((item) => item.name) };
}

/** 按工具要求裁剪回填给模型的内容。 */
function clipToolContent(content: string, limit: number): string {
  if (content.length <= limit) return content;
  return `${content.slice(0, limit)}\n…(内容过长,已截断,原始长度 ${content.length} 字符)`;
}

/** 把错误统一转成中文可读文案。 */
function describeError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return String(error);
}

function isCancelledError(error: unknown): boolean {
  return error instanceof AgentCancelledError;
}

function assertNotCancelled(signal: AbortSignal): void {
  if (signal.aborted) throw new AgentCancelledError();
}

/** 工具定义(JSON Schema),发给模型。 */
export const LOCAL_TOOL_NAMES = ['list_files', 'read_file', 'write_file', 'run_command'] as const;

const TOOL_DEFINITIONS = [
  {
    type: 'function',
    function: {
      name: 'list_files',
      description:
        '列出工作区里某个目录的内容(目录在前、文件在后,最多 2000 项)。路径相对工作区根目录,空字符串表示根目录。',
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: '相对工作区根目录的目录路径,例如 src 或 src/main;留空表示根目录。',
          },
        },
        required: [],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'read_file',
      description: '读取工作区里的一个文本文件(最多 256KB,超出会截断)。路径必须相对工作区根目录。',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '相对工作区根目录的文件路径,例如 src/App.tsx。' },
        },
        required: ['path'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'write_file',
      description:
        '创建或覆盖工作区里的一个文本文件,父目录不存在会自动创建。路径必须相对工作区根目录,禁止使用绝对路径。',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '相对工作区根目录的文件路径。' },
          content: { type: 'string', description: '要写入的完整文本内容(会整体覆盖原文件)。' },
        },
        required: ['path', 'content'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'run_command',
      description:
        '在工作区根目录执行一条 shell 命令(构建、测试、git、查看目录等),默认 120 秒超时,输出超过 256KB 会截断。',
      parameters: {
        type: 'object',
        properties: {
          command: { type: 'string', description: '要执行的完整命令,例如 npm test。' },
        },
        required: ['command'],
        additionalProperties: false,
      },
    },
  },
] as const;

/** 本地助手系统提示词。 */
export function buildSystemPrompt(workspaceRoot: string): string {
  return [
    '你是一个在用户本地电脑上工作的编程助手,运行在 beauty moon 桌面应用的「本地电脑模式」里。',
    `当前工作区根目录:${workspaceRoot}`,
    '',
    '可用工具:',
    '- list_files:查看目录内容,path 相对工作区根目录,空字符串表示根目录。',
    '- read_file:读取文本文件,path 相对工作区根目录。',
    '- write_file:创建或覆盖文件,path 必须相对工作区根目录,禁止使用绝对路径,也不要写到工作区之外。',
    '- run_command:在工作区根目录执行 shell 命令(构建、测试、git 等)。',
    '',
    '工作约定:',
    '1. 所有文件路径都相对工作区根目录,不能越出该目录;',
    '2. 先看再改:需要了解现状时先用 list_files / read_file,不要凭猜测写代码;',
    '3. 改动文件前先读取原文,写入时给出完整文件内容;',
    '4. 改完代码用 run_command 做必要验证(例如跑测试或类型检查);',
    '5. 用中文回答;先简要说明你要做什么,再调用工具,最后总结做了什么、验证结果如何;',
    '6. 不要重复读取同一个文件,也不要无意义地反复执行相同命令。',
  ].join('\n');
}

export class LocalAgent {
  private readonly sessions: AgentSessionStore;
  private readonly workspace: WorkspaceApi;
  private readonly runCommandImpl: LocalAgentOptions['runCommand'];
  private readonly readSettings: () => Promise<DesktopSettings>;
  private readonly onEvent: (event: AgentEvent) => void;
  private readonly fetchImpl: typeof fetch;
  private readonly maxIterations: number;
  private readonly maxToolOutputChars: number;

  /** sessionId → 取消控制器 */
  private readonly runs = new Map<string, AbortController>();
  /** sessionId → 当前执行任务(用于 cancel/waitForIdle 等待结束) */
  private readonly pending = new Map<string, Promise<void>>();

  constructor(options: LocalAgentOptions) {
    this.sessions = options.sessions;
    this.workspace = options.workspace;
    this.runCommandImpl = options.runCommand;
    this.readSettings = options.readSettings;
    this.onEvent = options.onEvent;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    this.maxIterations = options.maxIterations ?? DEFAULT_MAX_ITERATIONS;
    this.maxToolOutputChars = options.maxToolOutputChars ?? DEFAULT_MAX_TOOL_OUTPUT_CHARS;
  }

  /** 追加用户消息并异步开始一轮执行;返回时执行尚未结束,进度通过事件推送。 */
  async send(input: SendMessageInput): Promise<void> {
    const sessionId = typeof input?.sessionId === 'string' ? input.sessionId : '';
    const text = typeof input?.text === 'string' ? input.text.trim() : '';
    if (!sessionId) throw new Error('缺少会话 id。');
    if (!text) throw new Error('消息内容不能为空。');
    if (this.runs.has(sessionId)) {
      throw new Error('当前会话正在执行,请先停止上一轮任务。');
    }
    const session = await this.sessions.get(sessionId);
    if (!session) throw new Error('会话不存在,可能已被删除。');

    const appended = await this.sessions.appendMessage(sessionId, { role: 'user', content: text });
    this.emit({ type: 'session.updated', session: appended.session });

    const controller = new AbortController();
    this.runs.set(sessionId, controller);
    const task = this.executeRun(sessionId, controller.signal);
    this.pending.set(sessionId, task);
    // executeRun 内部已兜住所有异常,这里只需保证 Promise 不会被当成 unhandled rejection。
    void task;
  }

  /** 中止正在跑的一轮;等待其真正结束后 resolve(方便测试与退出流程)。 */
  async cancel(sessionId: string): Promise<void> {
    const controller = this.runs.get(sessionId);
    if (!controller) return;
    const task = this.pending.get(sessionId);
    controller.abort();
    if (task) await task.catch(() => undefined);
  }

  /** 等待当前会话的执行结束(没有执行则立即返回)。 */
  async waitForIdle(sessionId: string): Promise<void> {
    const task = this.pending.get(sessionId);
    if (!task) return;
    await task.catch(() => undefined);
  }

  isRunning(sessionId: string): boolean {
    return this.runs.has(sessionId);
  }

  /** 退出应用时中止所有会话。 */
  async shutdown(): Promise<void> {
    const tasks: Promise<void>[] = [];
    for (const [sessionId, controller] of this.runs) {
      controller.abort();
      const task = this.pending.get(sessionId);
      if (task) tasks.push(task.catch(() => undefined));
    }
    await Promise.all(tasks);
  }

  private emit(event: AgentEvent): void {
    try {
      this.onEvent(event);
    } catch (error) {
      // 事件消费方(窗口已销毁等)出错不能打断 Agent 循环。
      console.error('[local-agent] 推送事件失败:', describeError(error));
    }
  }

  private async executeRun(sessionId: string, signal: AbortSignal): Promise<void> {
    this.emit({ type: 'run.started', sessionId });
    let status: 'ok' | 'error' | 'cancelled' = 'ok';
    let errorText: string | undefined;
    try {
      await this.runLoop(sessionId, signal);
      if (signal.aborted) status = 'cancelled';
    } catch (error) {
      if (signal.aborted || isCancelledError(error)) {
        status = 'cancelled';
      } else {
        status = 'error';
        errorText = describeError(error);
      }
    } finally {
      this.runs.delete(sessionId);
      this.pending.delete(sessionId);
    }
    this.emit({ type: 'run.finished', sessionId, status, error: errorText });
  }

  private async runLoop(sessionId: string, signal: AbortSignal): Promise<void> {
    const settings = await this.readSettings();
    assertNotCancelled(signal);
    const baseUrl = (settings.model?.baseUrl ?? '').trim().replace(/\/+$/, '');
    const model = (settings.model?.model ?? '').trim();
    const apiKey = (settings.model?.apiKey ?? '').trim();
    if (!baseUrl) {
      throw new Error(
        '尚未配置模型服务地址(baseUrl)。请到设置里填写 OpenAI 兼容端点,例如 https://api.deepseek.com/v1。',
      );
    }
    if (!model) {
      throw new Error('尚未配置模型名称(model)。请到设置里填写模型名,例如 deepseek-chat。');
    }

    const session = await this.sessions.get(sessionId);
    if (!session) throw new Error('会话不存在,可能已被删除。');
    const workspaceRoot = (session.workspaceRoot ?? settings.workspaceRoot ?? '').trim();
    if (!workspaceRoot) {
      throw new Error('该会话尚未绑定工作区目录。请先选择本地工作区,再发送消息。');
    }

    const messages: OpenAiMessage[] = [
      { role: 'system', content: buildSystemPrompt(workspaceRoot) },
      ...this.replayHistory(session),
    ];

    for (let round = 0; round < this.maxIterations; round += 1) {
      assertNotCancelled(signal);

      // 先建一条空的助手消息,让渲染进程能立刻把增量渲染进正确的气泡。
      const placeholder = await this.sessions.appendMessage(sessionId, {
        role: 'assistant',
        content: '',
      });
      this.emit({ type: 'session.updated', session: placeholder.session });
      const messageId = placeholder.message.id;

      let streamed = '';
      let outcome: CompletionOutcome;
      try {
        outcome = await this.requestCompletion({
          baseUrl,
          apiKey,
          model,
          messages,
          signal,
          onDelta: (delta) => {
            streamed += delta;
            this.emit({ type: 'assistant.delta', sessionId, messageId, delta });
          },
        });
      } catch (error) {
        // 已经吐出的半截回答要保住;一个字都没有就把占位消息删掉。
        await this.finalizeAssistantMessage(sessionId, messageId, streamed).catch(() => undefined);
        throw error;
      }

      const assistantContent = outcome.content || streamed;
      await this.finalizeAssistantMessage(sessionId, messageId, assistantContent);

      if (outcome.toolCalls.length === 0) {
        return; // 模型认为任务结束
      }

      messages.push({
        role: 'assistant',
        content: assistantContent,
        tool_calls: outcome.toolCalls.map((call, index) => ({
          id: call.id || `call_${round}_${index}`,
          type: 'function',
          function: { name: call.name, arguments: call.arguments },
        })),
      });

      for (const [index, call] of outcome.toolCalls.entries()) {
        assertNotCancelled(signal);
        const toolName = call.name || 'unknown_tool';
        const running = await this.sessions.appendMessage(sessionId, {
          role: 'tool',
          content: `正在执行 ${toolName}…`,
          toolName,
          toolStatus: 'running',
        });
        this.emit({ type: 'session.updated', session: running.session });

        const result = await this.executeTool(toolName, call.arguments, workspaceRoot, signal);
        const finished = await this.sessions.updateMessage(sessionId, running.message.id, {
          content: result.content,
          toolStatus: result.ok ? 'ok' : 'error',
        });
        this.emit({ type: 'session.updated', session: finished });

        messages.push({
          role: 'tool',
          tool_call_id: call.id || `call_${round}_${index}`,
          content: result.content,
        });
      }
    }

    throw new Error(`已达到最大执行轮数(${this.maxIterations}),本轮已停止。可以让我继续处理剩余任务。`);
  }

  /** 助手占位消息收尾:有正文就更新,没正文就删掉(避免留下空气泡)。 */
  private async finalizeAssistantMessage(
    sessionId: string,
    messageId: string,
    content: string,
  ): Promise<void> {
    if (content.trim()) {
      const updated = await this.sessions.updateMessage(sessionId, messageId, { content });
      this.emit({ type: 'session.updated', session: updated });
    } else {
      const updated = await this.sessions.removeMessage(sessionId, messageId);
      this.emit({ type: 'session.updated', session: updated });
    }
  }

  /** 历史消息重放:只带 user / assistant 文本。 */
  private replayHistory(session: LocalSession): OpenAiMessage[] {
    const history: OpenAiMessage[] = [];
    for (const message of session.messages) {
      if (message.role === 'user' && message.content.trim()) {
        history.push({ role: 'user', content: message.content });
      } else if (message.role === 'assistant' && message.content.trim()) {
        history.push({ role: 'assistant', content: message.content });
      }
      // tool 消息只是界面展示用的过程记录;跨轮重放时跳过,
      // 因为契约里没有 tool_call_id,伪造会触发严格端点的 400。
    }
    return history;
  }

  /** 发起一次 chat/completions 请求,支持 SSE 流式与 JSON 兜底。 */
  private async requestCompletion(params: {
    baseUrl: string;
    apiKey: string;
    model: string;
    messages: OpenAiMessage[];
    signal: AbortSignal;
    onDelta: (delta: string) => void;
  }): Promise<CompletionOutcome> {
    const url = `${params.baseUrl}/chat/completions`;
    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          accept: 'text/event-stream',
          ...(params.apiKey ? { authorization: `Bearer ${params.apiKey}` } : {}),
        },
        body: JSON.stringify({
          model: params.model,
          messages: params.messages,
          tools: TOOL_DEFINITIONS,
          tool_choice: 'auto',
          stream: true,
        }),
        signal: params.signal,
      });
    } catch (error) {
      assertNotCancelled(params.signal);
      throw new Error(
        `无法连接模型端点(${url}):${describeError(error)}。请检查设置里的 baseUrl、网络连接或本地模型是否已启动。`,
      );
    }

    if (!response.ok) {
      const snippet = (await response.text().catch(() => '')).trim().slice(0, 2000);
      const statusText = response.statusText ? ` ${response.statusText}` : '';
      throw new Error(
        `模型端点返回 HTTP ${response.status}${statusText}:${snippet || '(无响应内容)'}。请检查 baseUrl、模型名与 API Key。`,
      );
    }

    const contentType = (response.headers.get('content-type') ?? '').toLowerCase();
    if (contentType.includes('application/json')) {
      const payload: unknown = await response.json().catch(() => null);
      return parseCompletionJson(payload, params.onDelta);
    }
    return this.consumeSse(response, params.signal, params.onDelta);
  }

  /** 解析 SSE 流:`data:` 行、`data: [DONE]`,按空行分块。 */
  private async consumeSse(
    response: Response,
    signal: AbortSignal,
    onDelta: (delta: string) => void,
  ): Promise<CompletionOutcome> {
    const body = response.body;
    if (!body) throw new Error('模型端点没有返回响应体。');

    const reader = body.getReader();
    const decoder = new TextDecoder();
    const state = createStreamState();
    let buffer = '';

    const handleBlock = (block: string): void => {
      for (const line of block.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        const data = trimmed.slice(5).trim();
        if (!data || data === '[DONE]') continue;
        let payload: unknown;
        try {
          payload = JSON.parse(data);
        } catch {
          continue; // 忽略心跳/非 JSON 注释行
        }
        applyStreamPayload(payload, state, onDelta);
      }
    };

    try {
      for (;;) {
        assertNotCancelled(signal);
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r\n/g, '\n');
        let boundary = buffer.indexOf('\n\n');
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          handleBlock(block);
          boundary = buffer.indexOf('\n\n');
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) handleBlock(buffer);
    } catch (error) {
      assertNotCancelled(signal);
      await reader.cancel().catch(() => undefined);
      throw new Error(`读取模型响应流失败:${describeError(error)}。`);
    }

    return { content: state.content, toolCalls: state.toolCalls.filter((item) => item.name) };
  }

  /** 执行一次工具调用,始终返回可读文本(失败也变成工具结果,让模型自己纠错)。 */
  private async executeTool(
    name: string,
    rawArguments: string,
    workspaceRoot: string,
    signal: AbortSignal,
  ): Promise<ToolOutcome> {
    const args = this.parseToolArguments(rawArguments);
    if (!args.ok) return { ok: false, content: args.error };

    try {
      switch (name) {
        case 'list_files':
          return await this.toolListFiles(workspaceRoot, args.value);
        case 'read_file':
          return await this.toolReadFile(workspaceRoot, args.value);
        case 'write_file':
          return await this.toolWriteFile(workspaceRoot, args.value);
        case 'run_command':
          return await this.runCommandTool(workspaceRoot, args.value, signal);
        default:
          return {
            ok: false,
            content: `未知工具「${name}」。可用工具:${LOCAL_TOOL_NAMES.join(' / ')}。`,
          };
      }
    } catch (error) {
      return { ok: false, content: `工具执行失败:${describeError(error)}` };
    }
  }

  private parseToolArguments(
    rawArguments: string,
  ): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
    const text = (rawArguments ?? '').trim();
    if (!text) return { ok: true, value: {} };
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: '工具参数必须是 JSON 对象。' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch {
      return { ok: false, error: `工具参数不是合法 JSON:${text.slice(0, 500)}` };
    }
  }

  private clip(content: string): string {
    return clipToolContent(content, this.maxToolOutputChars);
  }

  private async toolListFiles(workspaceRoot: string, args: Record<string, unknown>): Promise<ToolOutcome> {
    const relPath = typeof args.path === 'string' ? args.path : '';
    const entries = await this.workspace.listDirectory(workspaceRoot, relPath);
    const header = `目录 ${relPath || '.'} 共 ${entries.length} 项:`;
    const lines = entries
      .slice(0, 300)
      .map((entry: FileEntry) =>
        entry.kind === 'directory' ? `[目录] ${entry.path}/` : `[文件] ${entry.path} (${entry.size} B)`,
      );
    if (entries.length > lines.length) lines.push(`…(还有 ${entries.length - lines.length} 项未列出)`);
    return { ok: true, content: this.clip([header, ...lines].join('\n')) };
  }

  private async toolReadFile(workspaceRoot: string, args: Record<string, unknown>): Promise<ToolOutcome> {
    const relPath = typeof args.path === 'string' ? args.path.trim() : '';
    if (!relPath) return { ok: false, content: '缺少 path 参数:请给出相对工作区根目录的文件路径。' };
    const file = await this.workspace.readTextFile(workspaceRoot, relPath);
    const suffix = file.truncated ? '\n…(文件超过 256KB,内容已截断)' : '';
    return {
      ok: true,
      content: this.clip(`${file.path}(${file.size} 字节)\n\n${file.content}${suffix}`),
    };
  }

  private async toolWriteFile(workspaceRoot: string, args: Record<string, unknown>): Promise<ToolOutcome> {
    const relPath = typeof args.path === 'string' ? args.path.trim() : '';
    const content = typeof args.content === 'string' ? args.content : null;
    if (!relPath) return { ok: false, content: '缺少 path 参数:请给出相对工作区根目录的文件路径。' };
    if (content === null) return { ok: false, content: '缺少 content 参数:请给出要写入的完整文本内容。' };
    const written = await this.workspace.writeTextFile(workspaceRoot, relPath, content);
    return { ok: true, content: `已写入 ${written.path}(${written.bytes} 字节)。` };
  }

  private async runCommandTool(
    workspaceRoot: string,
    args: Record<string, unknown>,
    signal: AbortSignal,
  ): Promise<ToolOutcome> {
    const command = typeof args.command === 'string' ? args.command.trim() : '';
    if (!command) return { ok: false, content: '缺少 command 参数:请给出要执行的命令。' };
    assertNotCancelled(signal);
    const result = await this.runCommandImpl(command, { cwd: workspaceRoot, signal });
    return { ok: result.exitCode === 0, content: this.clip(this.formatCommandResult(result)) };
  }

  private formatCommandResult(result: CommandResult): string {
    const lines = [
      `退出码:${result.exitCode}${result.cancelled ? '(已取消)' : ''}`,
      `耗时:${result.durationMs}ms`,
    ];
    if (result.stdout.trim()) lines.push(`stdout:\n${result.stdout.trimEnd()}`);
    if (result.stderr.trim()) lines.push(`stderr:\n${result.stderr.trimEnd()}`);
    if (result.truncated) lines.push('(输出超过上限,已截断)');
    return lines.join('\n');
  }
}
