/**
 * 渲染进程的本地数据层:一个 React Context 收口 window.beautyDesktop 的全部状态。
 *
 * 为什么不用 react-query:桌面端的数据都在本机主进程,没有 HTTP 缓存/重试语义,
 * 而实时性靠 agent.subscribe 推送。这里用 reducer 把「一次拉取」与「事件流」
 * 收敛到同一份状态,避免两套数据源互相覆盖。
 *
 * 状态划分:
 * - 启动态(phase/bootError):settings.read + workspace.info + sessions.list 三件套;
 * - 会话态(activeSession/sessionLoading):当前打开会话的全量快照;
 * - 运行态(runningSessionId/runError):run.started / run.finished 驱动;
 * - 提示态(notice/actionError):写操作成功或失败的瞬时反馈。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import type {
  AgentEvent,
  ChatMessage,
  DesktopBridge,
  DesktopSettings,
  LocalMode,
  LocalSession,
  SessionSummary,
  WorkspaceInfo,
} from '@shared/contract';

import { errorMessage } from '@/lib/format';

export type DesktopPhase = 'loading' | 'ready' | 'error';

export interface DesktopState {
  phase: DesktopPhase;
  settings: DesktopSettings | null;
  workspace: WorkspaceInfo | null;
  sessions: SessionSummary[];
  activeSession: LocalSession | null;
  sessionLoading: boolean;
  runningSessionId: string | null;
  runError: { sessionId: string; message: string } | null;
  bootError: string | null;
  actionError: string | null;
  notice: string | null;
}

const INITIAL_STATE: DesktopState = {
  phase: 'loading',
  settings: null,
  workspace: null,
  sessions: [],
  activeSession: null,
  sessionLoading: false,
  runningSessionId: null,
  runError: null,
  bootError: null,
  actionError: null,
  notice: null,
};

type Action =
  | { type: 'boot-start' }
  | {
      type: 'ready';
      settings: DesktopSettings;
      workspace: WorkspaceInfo | null;
      sessions: SessionSummary[];
    }
  | { type: 'boot-error'; error: string }
  | { type: 'settings'; settings: DesktopSettings }
  | { type: 'workspace'; workspace: WorkspaceInfo }
  | { type: 'sessions'; sessions: SessionSummary[] }
  | { type: 'session-loading' }
  | { type: 'session-open'; session: LocalSession | null }
  | { type: 'session-created'; session: LocalSession }
  | { type: 'session-snapshot'; session: LocalSession }
  | { type: 'optimistic-user'; sessionId: string; content: string }
  | { type: 'event'; event: AgentEvent }
  | { type: 'run-started'; sessionId: string }
  | {
      type: 'run-finished';
      sessionId: string;
      status: 'ok' | 'error' | 'cancelled';
      error?: string;
    }
  | { type: 'notice'; notice: string | null }
  | { type: 'action-error'; error: string | null };

/** 会话全量快照 → 列表项。契约里 SessionSummary 是 LocalSession 去掉 messages。 */
export function toSessionSummary(session: LocalSession): SessionSummary {
  return {
    id: session.id,
    title: session.title,
    workspaceRoot: session.workspaceRoot,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messageCount,
  };
}

/** 把更新后的会话合并进列表:已存在则替换,不存在则插到最前。 */
function mergeSummary(
  sessions: SessionSummary[],
  summary: SessionSummary,
): SessionSummary[] {
  const index = sessions.findIndex((item) => item.id === summary.id);
  if (index === -1) return [summary, ...sessions];
  const next = [...sessions];
  next[index] = summary;
  return next;
}

/** 消息增量:找到助手消息追加正文,找不到就新建一条(支持先收到 delta)。 */
function appendDelta(
  messages: ChatMessage[],
  messageId: string,
  delta: string,
): ChatMessage[] {
  const index = messages.findIndex((message) => message.id === messageId);
  if (index === -1) {
    return [
      ...messages,
      {
        id: messageId,
        role: 'assistant',
        content: delta,
        createdAt: new Date().toISOString(),
      },
    ];
  }
  const next = [...messages];
  next[index] = { ...next[index], content: next[index].content + delta };
  return next;
}

function applyEvent(state: DesktopState, event: AgentEvent): DesktopState {
  switch (event.type) {
    case 'session.updated': {
      const summary = toSessionSummary(event.session);
      return {
        ...state,
        sessions: mergeSummary(state.sessions, summary),
        activeSession:
          state.activeSession?.id === event.session.id
            ? event.session
            : state.activeSession,
      };
    }
    case 'assistant.delta': {
      if (!state.activeSession || state.activeSession.id !== event.sessionId) {
        return state;
      }
      return {
        ...state,
        activeSession: {
          ...state.activeSession,
          messages: appendDelta(
            state.activeSession.messages,
            event.messageId,
            event.delta,
          ),
        },
      };
    }
    case 'run.started':
      return { ...state, runningSessionId: event.sessionId, runError: null };
    case 'run.finished': {
      const isActive = state.activeSession?.id === event.sessionId;
      return {
        ...state,
        runningSessionId:
          state.runningSessionId === event.sessionId
            ? null
            : state.runningSessionId,
        runError:
          event.status === 'error' && isActive
            ? {
                sessionId: event.sessionId,
                message: event.error || '本地执行失败',
              }
            : isActive
              ? null
              : state.runError,
      };
    }
    default:
      return state;
  }
}

function reducer(state: DesktopState, action: Action): DesktopState {
  switch (action.type) {
    case 'boot-start':
      return { ...state, phase: 'loading', bootError: null };
    case 'ready':
      return {
        ...state,
        phase: 'ready',
        settings: action.settings,
        workspace: action.workspace,
        sessions: action.sessions,
        bootError: null,
      };
    case 'boot-error':
      return { ...state, phase: 'error', bootError: action.error };
    case 'settings':
      return { ...state, settings: action.settings };
    case 'workspace':
      return { ...state, workspace: action.workspace };
    case 'sessions':
      return { ...state, sessions: action.sessions };
    case 'session-loading':
      return { ...state, sessionLoading: true, actionError: null };
    case 'session-open':
      return {
        ...state,
        activeSession: action.session,
        sessionLoading: false,
        runError: null,
      };
    case 'session-created':
      return {
        ...state,
        activeSession: action.session,
        sessions: mergeSummary(
          state.sessions,
          toSessionSummary(action.session),
        ),
        sessionLoading: false,
        runError: null,
      };
    case 'session-snapshot': {
      const summary = toSessionSummary(action.session);
      return {
        ...state,
        sessions: mergeSummary(state.sessions, summary),
        activeSession:
          state.activeSession?.id === action.session.id
            ? action.session
            : state.activeSession,
      };
    }
    case 'optimistic-user': {
      if (state.activeSession?.id !== action.sessionId) return state;
      const optimistic: ChatMessage = {
        id: `local-${Date.now()}`,
        role: 'user',
        content: action.content,
        createdAt: new Date().toISOString(),
      };
      return {
        ...state,
        activeSession: {
          ...state.activeSession,
          messages: [...state.activeSession.messages, optimistic],
        },
      };
    }
    case 'event':
      return applyEvent(state, action.event);
    case 'run-started':
      return { ...state, runningSessionId: action.sessionId, runError: null };
    case 'run-finished':
      return {
        ...state,
        runningSessionId:
          state.runningSessionId === action.sessionId
            ? null
            : state.runningSessionId,
        runError:
          action.status === 'error'
            ? {
                sessionId: action.sessionId,
                message: action.error || '本地执行失败',
              }
            : null,
      };
    case 'notice':
      return { ...state, notice: action.notice };
    case 'action-error':
      return { ...state, actionError: action.error };
    default:
      return state;
  }
}

export interface DesktopActions {
  /** 重新执行启动读取(设置读取失败时的重试入口)。 */
  reload(): void;
  refreshSessions(): Promise<void>;
  openSession(id: string): Promise<void>;
  createSession(): Promise<void>;
  renameSession(id: string, title: string): Promise<void>;
  removeSession(id: string): Promise<void>;
  chooseWorkspace(): Promise<void>;
  switchMode(mode: LocalMode): Promise<void>;
  saveSettings(patch: Partial<DesktopSettings>): Promise<void>;
  sendMessage(text: string): Promise<void>;
  cancelRun(): Promise<void>;
  dismissNotice(): void;
}

export interface DesktopContextValue {
  bridge: DesktopBridge;
  state: DesktopState;
  actions: DesktopActions;
}

const DesktopContext = createContext<DesktopContextValue | null>(null);

export function DesktopProvider({
  bridge,
  children,
}: {
  bridge: DesktopBridge;
  children: ReactNode;
}) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [bootAttempt, setBootAttempt] = useState(0);

  // 回调里需要读最新状态,但又不想让回调身份随状态变化:用 ref 镜像。
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    let cancelled = false;
    dispatch({ type: 'boot-start' });
    // settings 是启动的硬依赖;工作区与会话列表失败时降级为空,不让整个界面挂掉
    // (例如首次启动 userData 里还没有 sessions 目录)。
    bridge.settings
      .read()
      .then(async (settings) => {
        const [workspace, sessions] = await Promise.all([
          bridge.workspace.info().catch(() => null),
          bridge.sessions.list().catch(() => [] as SessionSummary[]),
        ]);
        if (cancelled) return;
        dispatch({ type: 'ready', settings, workspace, sessions });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        dispatch({ type: 'boot-error', error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, bootAttempt]);

  // 主进程事件流:整份会话快照 + 正文增量 + 运行起止。
  useEffect(
    () =>
      bridge.agent.subscribe((event) => {
        dispatch({ type: 'event', event });
      }),
    [bridge],
  );

  // 提示语自动消失,避免「已保存」一直挂在界面上。
  useEffect(() => {
    if (!state.notice) return;
    const timer = window.setTimeout(
      () => dispatch({ type: 'notice', notice: null }),
      2400,
    );
    return () => window.clearTimeout(timer);
  }, [state.notice]);

  const fail = useCallback((error: unknown) => {
    dispatch({ type: 'action-error', error: errorMessage(error) });
  }, []);

  const reload = useCallback(() => {
    setBootAttempt((value) => value + 1);
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      dispatch({ type: 'sessions', sessions: await bridge.sessions.list() });
    } catch (error) {
      fail(error);
    }
  }, [bridge, fail]);

  const openSession = useCallback(
    async (id: string) => {
      dispatch({ type: 'session-loading' });
      try {
        const session = await bridge.sessions.get(id);
        dispatch({ type: 'session-open', session });
      } catch (error) {
        dispatch({ type: 'session-open', session: null });
        fail(error);
      }
    },
    [bridge, fail],
  );

  const chooseWorkspace = useCallback(async () => {
    try {
      const workspace = await bridge.workspace.choose();
      if (!workspace) return;
      dispatch({ type: 'workspace', workspace });
      // 主进程会把选中的目录写进 settings.workspaceRoot,重读一次保持一致。
      const settings = await bridge.settings.read();
      dispatch({ type: 'settings', settings });
      dispatch({ type: 'sessions', sessions: await bridge.sessions.list() });
    } catch (error) {
      fail(error);
    }
  }, [bridge, fail]);

  const createSession = useCallback(async () => {
    try {
      if (!stateRef.current.workspace) {
        // 没有工作区时「新建会话」退化为选择目录,避免主进程返回不可读错误。
        await chooseWorkspace();
        return;
      }
      const session = await bridge.sessions.create();
      dispatch({ type: 'session-created', session });
      dispatch({ type: 'sessions', sessions: await bridge.sessions.list() });
    } catch (error) {
      fail(error);
    }
  }, [bridge, chooseWorkspace, fail]);

  const renameSession = useCallback(
    async (id: string, title: string) => {
      const trimmed = title.trim();
      if (!trimmed) return;
      try {
        dispatch({
          type: 'session-snapshot',
          session: await bridge.sessions.rename(id, trimmed),
        });
        dispatch({ type: 'sessions', sessions: await bridge.sessions.list() });
      } catch (error) {
        fail(error);
      }
    },
    [bridge, fail],
  );

  const removeSession = useCallback(
    async (id: string) => {
      try {
        await bridge.sessions.remove(id);
        if (stateRef.current.activeSession?.id === id) {
          dispatch({ type: 'session-open', session: null });
        }
        dispatch({ type: 'sessions', sessions: await bridge.sessions.list() });
        dispatch({ type: 'notice', notice: '会话已删除' });
      } catch (error) {
        fail(error);
      }
    },
    [bridge, fail],
  );

  const switchMode = useCallback(
    async (mode: LocalMode) => {
      try {
        const settings = await bridge.settings.update({ mode });
        dispatch({ type: 'settings', settings });
      } catch (error) {
        fail(error);
      }
    },
    [bridge, fail],
  );

  const saveSettings = useCallback(
    async (patch: Partial<DesktopSettings>) => {
      try {
        const settings = await bridge.settings.update(patch);
        dispatch({ type: 'settings', settings });
        dispatch({ type: 'notice', notice: '设置已保存' });
      } catch (error) {
        fail(error);
      }
    },
    [bridge, fail],
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const session = stateRef.current.activeSession;
      const content = text.trim();
      if (!session || !content) return;
      dispatch({ type: 'optimistic-user', sessionId: session.id, content });
      dispatch({ type: 'run-started', sessionId: session.id });
      try {
        await bridge.agent.send({ sessionId: session.id, text: content });
      } catch (error) {
        dispatch({
          type: 'run-finished',
          sessionId: session.id,
          status: 'error',
          error: errorMessage(error),
        });
        fail(error);
      }
    },
    [bridge, fail],
  );

  const cancelRun = useCallback(async () => {
    const sessionId =
      stateRef.current.runningSessionId ??
      stateRef.current.activeSession?.id ??
      null;
    if (!sessionId) return;
    try {
      await bridge.agent.cancel(sessionId);
      dispatch({ type: 'notice', notice: '已请求停止当前执行' });
    } catch (error) {
      fail(error);
    }
  }, [bridge, fail]);

  const dismissNotice = useCallback(() => {
    dispatch({ type: 'action-error', error: null });
    dispatch({ type: 'notice', notice: null });
  }, []);

  const actions = useMemo<DesktopActions>(
    () => ({
      reload,
      refreshSessions,
      openSession,
      createSession,
      renameSession,
      removeSession,
      chooseWorkspace,
      switchMode,
      saveSettings,
      sendMessage,
      cancelRun,
      dismissNotice,
    }),
    [
      reload,
      refreshSessions,
      openSession,
      createSession,
      renameSession,
      removeSession,
      chooseWorkspace,
      switchMode,
      saveSettings,
      sendMessage,
      cancelRun,
      dismissNotice,
    ],
  );

  const value = useMemo<DesktopContextValue>(
    () => ({ bridge, state, actions }),
    [bridge, state, actions],
  );

  return (
    <DesktopContext.Provider value={value}>{children}</DesktopContext.Provider>
  );
}

export function useDesktop(): DesktopContextValue {
  const value = useContext(DesktopContext);
  if (!value) {
    throw new Error('useDesktop 必须在 DesktopProvider 内使用');
  }
  return value;
}
