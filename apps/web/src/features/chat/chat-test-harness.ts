import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentType, ReactNode } from 'react';
import { createElement } from 'react';
import { MemoryRouter, useLocation } from 'react-router';
import { vi } from 'vitest';

import type { ChatMessage, Conversation } from '@/lib/conversations-api';
import type { MCPServer } from '@/lib/mcp-api';
import type { Run, RunEvent } from '@/lib/runs-api';
import type { Workspace } from '@/lib/workspace-api';

/**
 * ChatPage 测试共享装置。刻意不 import 被 mock 的模块与 pages/chat:
 * 各测试文件用 vi.mock 工厂动态 import 本文件,避免模块循环。
 */

export const workspace: Workspace = {
  id: 'ws-1',
  name: '默认工作区',
  description: '自动创建:租户默认工作区',
  status: 'active',
  created_at: '2026-10-13T00:00:00+00:00',
};

export function makeConversation(
  overrides: Partial<Conversation> = {},
): Conversation {
  return {
    id: 'c1',
    title: '整理本周日程成周报',
    workspace_id: workspace.id,
    created_at: '2026-10-13T10:00:00+00:00',
    updated_at: '2026-10-13T10:05:00+00:00',
    active_run_id: 'run-1',
    ...overrides,
  };
}

export function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm1',
    conversation_id: 'c1',
    run_id: 'run-1',
    role: 'user',
    content: '把本周日程整理成周报',
    created_at: '2026-10-13T10:01:00+00:00',
    ...overrides,
  };
}

export function makeMcpServer(overrides: Partial<MCPServer> = {}): MCPServer {
  return {
    id: 'mcp-linear',
    name: 'linear',
    transport: 'http',
    url: 'https://mcp.linear.app/mcp',
    enabled: true,
    auto_approve: false,
    has_token: false,
    token_hint: null,
    command: null,
    args: [],
    runtime: 'node',
    image: null,
    has_env: false,
    env_keys: [],
    tools_allowlist: [],
    tools: [],
    timeout_seconds: 30,
    last_status: 'ok',
    last_checked_at: null,
    last_error: null,
    has_oauth: false,
    oauth_authorized: false,
    oauth_scopes: [],
    oauth_expires_at: null,
    oauth_authorization_server: null,
    oauth_error: null,
    created_at: '2026-10-13T10:00:00+00:00',
    ...overrides,
  };
}

export function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周日程成周报',
    goal: '汇总本周日程,生成周报草稿',
    status: 'awaiting_approval',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: [],
    status_reason: null,
    parent_run_id: null,
    conversation_id: 'c1',
    created_at: '2026-10-13T10:00:00+00:00',
    ...overrides,
  };
}

export function event(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
): RunEvent {
  return { seq, type, payload };
}

// SSE 管道由 lib/sse 测试覆盖;这里只测"事件 → 会话"的映射与就地动作
let currentEvents: RunEvent[] = [];
export function getRunEvents(): RunEvent[] {
  return currentEvents;
}
export function setRunEvents(events: RunEvent[]): void {
  currentEvents = events;
}

/** 把当前地址渲染出来,供「切工作区后 URL 是否保留 new=1」这类断言使用。 */
export function LocationProbe() {
  const location = useLocation();
  return createElement(
    'span',
    { 'data-testid': 'location' },
    location.pathname + location.search,
  );
}

export function createRenderPage(Page: ComponentType) {
  return function renderPage(initialEntry = '/', extra?: ReactNode) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(
          MemoryRouter,
          { initialEntries: [initialEntry] },
          createElement(Page),
          extra,
        ),
      ),
    );
    // queryClient 供需要手动推进缓存(如 run 终态)的用例使用
    return { ...view, queryClient };
  };
}

export function typeAndSend(text: string): void {
  fireEvent.change(screen.getByLabelText('描述下一步任务'), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: '发送' }));
}

export async function setupChatTest(): Promise<void> {
  vi.clearAllMocks();
  setRunEvents([]);
  const { conversationsApi, streamMessage } = await import(
    '@/lib/conversations-api'
  );
  const { mcpApi } = await import('@/lib/mcp-api');
  const { skillsApi } = await import('@/lib/skills-api');
  const { workspaceApi } = await import('@/lib/workspace-api');
  const { runsApi } = await import('@/lib/runs-api');

  vi.mocked(workspaceApi.workspaces).mockResolvedValue([workspace]);
  vi.mocked(workspaceApi.defaultWorkspace).mockResolvedValue(workspace);
  vi.mocked(conversationsApi.list).mockResolvedValue([makeConversation()]);
  vi.mocked(conversationsApi.create).mockResolvedValue(
    makeConversation({
      id: 'c-new',
      title: '新会话',
      active_run_id: undefined,
    }),
  );
  vi.mocked(conversationsApi.get).mockResolvedValue(makeConversation());
  vi.mocked(conversationsApi.messages).mockResolvedValue([makeMessage()]);
  // 一个启用 server + 一个停用 server:选择器只应出现前者
  vi.mocked(mcpApi.list).mockResolvedValue([
    makeMcpServer(),
    makeMcpServer({ id: 'mcp-legacy', name: '旧 server', enabled: false }),
  ]);
  vi.mocked(skillsApi.list).mockResolvedValue([]);
  vi.mocked(runsApi.getPlan).mockResolvedValue({
    run_id: 'run-1',
    status: 'pending',
    plan_markdown: '',
  });
  // 默认:分流为任务(建 run),与流式端点同形状
  vi.mocked(streamMessage).mockImplementation(
    async (_conversationId, _content, _mode, handlers) => {
      handlers.onMeta?.({
        mode: 'run_created',
        message: makeMessage({
          id: 'm9',
          conversation_id: 'c-new',
          run_id: 'run-9',
          content: '把本周日程整理成周报',
        }),
        run: makeRun({
          id: 'run-9',
          status: 'awaiting_approval',
          conversation_id: 'c-new',
        }),
      });
      handlers.onDone?.({ mode: 'run_created', reply: null });
    },
  );
  vi.mocked(runsApi.get).mockResolvedValue(makeRun());
  vi.mocked(runsApi.steps).mockResolvedValue([]);
}

/* ── vi.mock 工厂:测试文件里动态 import 本文件后调用 ─────────────── */

export function conversationsModuleMock() {
  return {
    conversationsApi: {
      list: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      messages: vi.fn(),
      postMessage: vi.fn(),
      attachments: vi.fn(async () => []),
      uploadAttachment: vi.fn(),
      downloadAttachment: vi.fn(),
    },
    streamMessage: vi.fn(),
  };
}

export function skillsModuleMock() {
  return { skillsApi: { list: vi.fn(async () => []), get: vi.fn() } };
}

export function mcpModuleMock() {
  return { mcpApi: { list: vi.fn(async () => []) } };
}

export async function workspaceModuleMock() {
  const actual = await vi.importActual<typeof import('@/lib/workspace-api')>(
    '@/lib/workspace-api',
  );
  return {
    ...actual,
    workspaceApi: { workspaces: vi.fn(), defaultWorkspace: vi.fn() },
  };
}

export async function runsModuleMock() {
  const actual =
    await vi.importActual<typeof import('@/lib/runs-api')>('@/lib/runs-api');
  return {
    ...actual,
    runsApi: {
      get: vi.fn(),
      getPlan: vi.fn(),
      steps: vi.fn(async () => []),
      approve: vi.fn(),
      reject: vi.fn(),
      confirm: vi.fn(),
      answerRunQuestion: vi.fn(),
      artifacts: vi.fn(async () => []),
      downloadArtifact: vi.fn(),
    },
  };
}

export function useRunEventsModuleMock() {
  return {
    useRunEvents: () => ({ events: getRunEvents(), connected: true }),
  };
}
